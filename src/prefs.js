/*
 * Voice Assistant GNOME Extension
 * Copyright (C) 2026 Giorgio Dramis
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <https://www.gnu.org/licenses/>.
 */

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Gtk from 'gi://Gtk';
import Gdk from 'gi://Gdk';
import Adw from 'gi://Adw';
import Soup from 'gi://Soup';
import { ExtensionPreferences } from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

export default class VoiceAssistantPreferences extends ExtensionPreferences {
    fillPreferencesWindow(window) {

        // Caricamento e registrazione di GResource ed IconTheme per la finestra delle preferenze
        try {
            const gresourcePath = `${this.path}/org.gnome.shell.extensions.voice-assistant.gresource`;
            const resourceFile = Gio.File.new_for_path(gresourcePath);
            if (resourceFile.query_exists(null)) {
                const resource = Gio.Resource.load(gresourcePath);
                Gio.resources_register(resource);
            }

            const display = window.get_display() || Gdk.Display.get_default();
            if (display) {
                const iconTheme = Gtk.IconTheme.get_for_display(display);
                iconTheme.add_resource_path('/org/gnome/shell/extensions/voice-assistant/icons');

                const iconsDir = `${this.path}/icons`;
                if (Gio.File.new_for_path(iconsDir).query_exists(null)) {
                    iconTheme.add_search_path(iconsDir);
                }
            }
        } catch (e) {
            console.warn('[VoiceAssistant] Impossibile registrare le risorse icona in prefs:', e);
        }

        const _ = this.gettext.bind(this);
        const settings = this.getSettings('org.gnome.shell.extensions.voice-assistant');

        // Dimensioni di default per desktop
        window.set_default_size(860, 600);

        // Caricamento interfaccia dal file Blueprint (compilato in prefs.ui nelle risorse)
        // Il dominio gettext DEVE essere impostato PRIMA di caricare il file UI,
        // altrimenti le stringhe translatable nel .ui non vengono tradotte.
        const builder = new Gtk.Builder();
        builder.set_translation_domain(this.metadata['gettext-domain'] || 'voice-assistant');
        builder.add_from_resource('/org/gnome/shell/extensions/voice-assistant/ui/prefs.ui');
        const splitView = builder.get_object('split_view');

        // Breakpoint per supporto responsive mobile (< 600px)
        const mobileBreakpoint = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse('max-width: 600px')
        );
        mobileBreakpoint.add_setter(splitView, 'collapsed', true);
        window.add_breakpoint(mobileBreakpoint);

        // Helper per i percorsi dei modelli
        const getModelsPath = () => {
            let customPath = settings.get_string('models-dir');
            if (customPath && customPath.trim().length > 0) {
                if (customPath.startsWith('~/')) {
                    return GLib.get_home_dir() + customPath.substring(1);
                }
                return customPath;
            }
            return GLib.get_home_dir() + '/.local/share/voice-assistant/models';
        };

        const formatPathForDisplay = (path) => {
            const home = GLib.get_home_dir();
            if (path.startsWith(home)) {
                return '~' + path.substring(home.length);
            }
            return path;
        };

        const getDirSize = (dirPath) => {
            try {
                let proc = new Gio.Subprocess({
                    argv: ['du', '-sb', dirPath],
                    flags: Gio.SubprocessFlags.STDOUT_PIPE
                });
                proc.init(null);
                let [, stdout] = proc.communicate_utf8(null, null);
                if (stdout) {
                    let bytes = parseInt(stdout.split('\t')[0]);
                    if (bytes >= 1073741824) return `${(bytes / 1073741824).toFixed(1)} GB`;
                    if (bytes >= 1048576) return `${(bytes / 1048576).toFixed(0)} MB`;
                    if (bytes >= 1024) return `${(bytes / 1024).toFixed(0)} KB`;
                    return `${bytes} B`;
                }
            } catch (e) { }
            return '?';
        };

        // ==========================================
        // 1. GENERALI: BINDINGS E EVENTI
        // ==========================================
        const enableSwitchRow = builder.get_object('enable_switch_row');
        settings.bind('enabled', enableSwitchRow, 'active', Gio.SettingsBindFlags.DEFAULT);

        const langRow = builder.get_object('lang_row');
        let currentLang = settings.get_string('language') || 'it';
        langRow.selected = (currentLang === 'en') ? 1 : 0;
        langRow.connect('notify::selected', () => {
            let newLang = (langRow.selected === 1) ? 'en' : 'it';
            settings.set_string('language', newLang);
            if (typeof renderModelList === 'function') renderModelList();
        });

        const wakewordRow = builder.get_object('wakeword_row');
        settings.bind('wakeword', wakewordRow, 'text', Gio.SettingsBindFlags.DEFAULT);


        // ==========================================
        // 2. MOTORE VOCALE (STT) E DOWNLOAD STATE
        // ==========================================
        let activeProvider = settings.get_string('stt-provider') || 'vosk';
        let activeModel = settings.get_string('stt-model') || 'vosk-model-small-it-0.22';

        const downloadingProgress = new Map();
        const downloadButtons = new Map();
        let renderModelList = () => { };
        let refreshCacheGroup = () => { };

        const queryDownloadingModels = (callback) => {
            try {
                Gio.DBus.session.call(
                    'org.local.VoiceAssistant',
                    '/org/local/VoiceAssistant',
                    'org.local.VoiceAssistant',
                    'GetDownloadingModels',
                    null,
                    new GLib.VariantType('(s)'),
                    Gio.DBusCallFlags.NONE,
                    -1,
                    null,
                    (source, res) => {
                        try {
                            let [jsonStr] = source.call_finish(res).unpack();
                            let obj = JSON.parse(jsonStr);
                            downloadingProgress.clear();
                            for (let k in obj) {
                                downloadingProgress.set(k, obj[k]);
                            }
                        } catch (e) { }
                        if (typeof callback === 'function') callback();
                    }
                );
            } catch (e) {
                if (typeof callback === 'function') callback();
            }
        };

        queryDownloadingModels(() => {
            if (typeof renderModelList === 'function') renderModelList();
        });

        try {
            Gio.DBus.session.signal_subscribe(
                'org.local.VoiceAssistant',
                'org.local.VoiceAssistant',
                'DownloadProgress',
                '/org/local/VoiceAssistant',
                null,
                Gio.DBusSignalFlags.NONE,
                (connection, senderName, objectPath, interfaceName, signalName, parameters) => {
                    try {
                        let unpacked = parameters.deepUnpack ? parameters.deepUnpack() : parameters.unpack();
                        let [pName, mName, percent] = unpacked;
                        let key = `${pName}:${mName}`;
                        if (percent >= 0 && percent < 100) {
                            downloadingProgress.set(key, percent);
                            if (downloadButtons.has(key)) {
                                let { progressBar } = downloadButtons.get(key);
                                if (progressBar) {
                                    progressBar.fraction = Math.min(1.0, Math.max(0.0, percent / 100.0));
                                    progressBar.text = `${percent}%`;
                                }
                            } else {
                                GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
                                    renderModelList();
                                    return GLib.SOURCE_REMOVE;
                                });
                            }
                        } else {
                            downloadingProgress.delete(key);
                            downloadButtons.delete(key);
                            GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
                                renderModelList();
                                if (typeof refreshCacheGroup === 'function') refreshCacheGroup();
                                return GLib.SOURCE_REMOVE;
                            });
                        }
                    } catch (e) {
                        console.error('DownloadProgress signal handler error:', e);
                    }
                }
            );
        } catch (e) { }

        const currentModelRow = builder.get_object('current_model_row');
        const openModelSelectorBtn = builder.get_object('open_model_selector_btn');
        const whisperHardwareGroup = builder.get_object('whisper_hardware_group');
        const hwCpuRow = builder.get_object('hw_cpu_row');
        const hwCpuRadio = builder.get_object('hw_cpu_radio');
        const hwCudaRow = builder.get_object('hw_cuda_row');
        const hwCudaRadio = builder.get_object('hw_cuda_radio');

        let currentHw = settings.get_string('stt-hardware') || 'cpu';
        if (currentHw === 'cuda') hwCudaRadio.active = true;
        else hwCpuRadio.active = true;

        const selectHw = (hwId) => {
            if (hwId === 'cuda') hwCudaRadio.active = true;
            else hwCpuRadio.active = true;
            settings.set_string('stt-hardware', hwId);
        };

        hwCpuRow.connect('activated', () => selectHw('cpu'));
        hwCudaRow.connect('activated', () => selectHw('cuda'));
        hwCpuRadio.connect('toggled', () => { if (hwCpuRadio.active) settings.set_string('stt-hardware', 'cpu'); });
        hwCudaRadio.connect('toggled', () => { if (hwCudaRadio.active) settings.set_string('stt-hardware', 'cuda'); });

        const updateActiveModelSubtitle = () => {
            let provider = settings.get_string('stt-provider') || 'vosk';
            let model = settings.get_string('stt-model') || 'vosk-model-small-it-0.22';
            let providerDisplay = provider === 'whisper' ? 'Whisper' : 'Vosk';
            currentModelRow.subtitle = `${providerDisplay} • ${model}`;
            whisperHardwareGroup.visible = (provider === 'whisper');
        };
        updateActiveModelSubtitle();

        settings.connect('changed::stt-provider', updateActiveModelSubtitle);
        settings.connect('changed::stt-model', updateActiveModelSubtitle);


        // ==========================================
        // SELETTORE MODELLI (DISPOSIZIONE DINAMICA)
        // ==========================================
        const whisperStaticModels = [
            { id: 'tiny', provider: 'whisper', name: 'Whisper Tiny', subtitle: 'Whisper • ~75MB • Multilingual', lang: 'multilingual', lang_text: 'Multilingual', size_text: '~75MB' },
            { id: 'tiny.en', provider: 'whisper', name: 'Whisper Tiny (English)', subtitle: 'Whisper • ~75MB • English Only', lang: 'en', lang_text: 'English', size_text: '~75MB' },
            { id: 'base', provider: 'whisper', name: 'Whisper Base (Recommended)', subtitle: 'Whisper • ~140MB • Multilingual', lang: 'multilingual', lang_text: 'Multilingual', size_text: '~140MB' },
            { id: 'base.en', provider: 'whisper', name: 'Whisper Base (English)', subtitle: 'Whisper • ~140MB • English Only', lang: 'en', lang_text: 'English', size_text: '~140MB' },
            { id: 'small', provider: 'whisper', name: 'Whisper Small', subtitle: 'Whisper • ~466MB • Multilingual', lang: 'multilingual', lang_text: 'Multilingual', size_text: '~466MB' },
            { id: 'small.en', provider: 'whisper', name: 'Whisper Small (English)', subtitle: 'Whisper • ~466MB • English Only', lang: 'en', lang_text: 'English', size_text: '~466MB' },
            { id: 'medium', provider: 'whisper', name: 'Whisper Medium', subtitle: 'Whisper • ~1.5GB • Multilingual', lang: 'multilingual', lang_text: 'Multilingual', size_text: '~1.5GB' },
            { id: 'medium.en', provider: 'whisper', name: 'Whisper Medium (English)', subtitle: 'Whisper • ~1.5GB • English Only', lang: 'en', lang_text: 'English', size_text: '~1.5GB' },
            { id: 'large-v3', provider: 'whisper', name: 'Whisper Large v3', subtitle: 'Whisper • ~3.1GB • Multilingual', lang: 'multilingual', lang_text: 'Multilingual', size_text: '~3.1GB' }
        ];

        let fetchedVoskModels = [
            { id: 'vosk-model-small-it-0.22', provider: 'vosk', name: 'Italian - vosk-model-small-it-0.22', subtitle: 'Vosk • 47.4MiB • Italian', lang: 'it', lang_text: 'Italian', size_text: '47.4MiB', url: 'https://alphacephei.com/vosk/models/vosk-model-small-it-0.22.zip' },
            { id: 'vosk-model-it-0.22', provider: 'vosk', name: 'Italian - vosk-model-it-0.22', subtitle: 'Vosk • 1.2GiB • Italian', lang: 'it', lang_text: 'Italian', size_text: '1.2GiB', url: 'https://alphacephei.com/vosk/models/vosk-model-it-0.22.zip' },
            { id: 'vosk-model-small-en-us-0.15', provider: 'vosk', name: 'English - vosk-model-small-en-us-0.15', subtitle: 'Vosk • 40MiB • English', lang: 'en', lang_text: 'English', size_text: '40MiB', url: 'https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip' },
            { id: 'vosk-model-en-us-0.22', provider: 'vosk', name: 'English - vosk-model-en-us-0.22', subtitle: 'Vosk • 1.8GiB • English', lang: 'en', lang_text: 'English', size_text: '1.8GiB', url: 'https://alphacephei.com/vosk/models/vosk-model-en-us-0.22.zip' }
        ];

        const searchEntry = builder.get_object('search_entry');
        const selectorViewStack = builder.get_object('selector_view_stack');
        const selectorViewSwitcher = builder.get_object('selector_view_switcher');
        const selectorViewSwitcherBar = builder.get_object('selector_view_switcher_bar');
        const modelSelectorPage = builder.get_object('model_selector_page');

        const modelsGroupAll = builder.get_object('models_group_all');
        const modelsGroupInstalled = builder.get_object('models_group_installed');
        const modelsGroupDownloading = builder.get_object('models_group_downloading');

        const updateResponsiveSwitcher = () => {
            let width = window.default_width || window.get_allocated_width();
            let isNarrow = (width < 650);
            selectorViewSwitcherBar.reveal = isNarrow;
            selectorViewSwitcher.visible = !isNarrow;
        };

        window.connect('notify::default-width', updateResponsiveSwitcher);
        updateResponsiveSwitcher();

        selectorViewStack.connect('notify::visible-child-name', () => renderModelList());
        searchEntry.connect('search-changed', () => renderModelList());

        let activeModelGroupRows = [];

        renderModelList = () => {
            for (const item of activeModelGroupRows) {
                try {
                    item.group.remove(item.row);
                } catch (e) { }
            }
            activeModelGroupRows = [];

            const currentModelsPath = getModelsPath();
            const modelsDir = Gio.File.new_for_path(currentModelsPath);
            const installedSet = new Set();
            try {
                let enumerator = modelsDir.enumerate_children('standard::name,standard::type', Gio.FileQueryInfoFlags.NONE, null);
                let info;
                while ((info = enumerator.next_file(null)) !== null) {
                    if (info.get_file_type() === Gio.FileType.DIRECTORY) {
                        installedSet.add(info.get_name());
                    }
                }
            } catch (e) { }

            const allModels = [...whisperStaticModels, ...fetchedVoskModels];
            const query = searchEntry.text.trim().toLowerCase();
            const activeTab = selectorViewStack.visible_child_name || 'all';

            let targetGroup = modelsGroupAll;
            if (activeTab === 'installed') targetGroup = modelsGroupInstalled;
            else if (activeTab === 'downloading') targetGroup = modelsGroupDownloading;

            const filteredModels = allModels.filter(m => {
                let modelKey = `${m.provider}:${m.id}`;
                let isDownloading = downloadingProgress.has(modelKey);
                let isInstalled = !isDownloading && (installedSet.has(m.id) || installedSet.has(`${m.provider}-${m.id}`) || (m.provider === 'whisper' && installedSet.has(`whisper-${m.id}`)));

                if (activeTab === 'installed' && !isInstalled) return false;
                if (activeTab === 'downloading' && !isDownloading) return false;

                if (query.length > 0) {
                    let text = `${m.name} ${m.id} ${m.provider} ${m.lang} ${m.lang_text} ${m.size_text}`.toLowerCase();
                    if (!text.includes(query)) return false;
                }
                return true;
            });

            if (filteredModels.length === 0) {
                const emptyRow = new Adw.ActionRow({
                    title: _('No models found'),
                    subtitle: _('Try changing search query or tab.')
                });
                targetGroup.add(emptyRow);
                activeModelGroupRows.push({ row: emptyRow, group: targetGroup });
                return;
            }

            const modelGroupLeader = new Gtk.CheckButton();

            filteredModels.forEach(m => {
                let currentProvider = settings.get_string('stt-provider') || 'vosk';
                let currentModel = settings.get_string('stt-model') || 'vosk-model-small-it-0.22';
                let isCurrent = (currentProvider === m.provider && currentModel === m.id);
                let modelKey = `${m.provider}:${m.id}`;
                let isDownloading = downloadingProgress.has(modelKey);
                let downloadPct = downloadingProgress.get(modelKey) || 0;
                let isInstalled = !isDownloading && (installedSet.has(m.id) || installedSet.has(`${m.provider}-${m.id}`) || (m.provider === 'whisper' && installedSet.has(`whisper-${m.id}`)));

                const row = new Adw.ActionRow({
                    title: m.name,
                    subtitle: m.subtitle || `${m.provider.toUpperCase()} • ${m.size_text || ''}`,
                    activatable: true
                });

                let checkBtn = null;
                if (isInstalled) {
                    checkBtn = new Gtk.CheckButton({
                        valign: Gtk.Align.CENTER,
                        margin_end: 12
                    });

                    checkBtn.set_group(modelGroupLeader);

                    if (isCurrent) {
                        checkBtn.active = true;
                    }

                    row.add_prefix(checkBtn);
                }

                const selectModel = () => {
                    if (checkBtn) checkBtn.active = true;
                    let currP = settings.get_string('stt-provider');
                    let currM = settings.get_string('stt-model');
                    if (currP !== m.provider || currM !== m.id) {
                        settings.set_string('stt-provider', m.provider);
                        settings.set_string('stt-model', m.id);
                        updateActiveModelSubtitle();
                        renderModelList();
                        if (typeof refreshCacheGroup === 'function') refreshCacheGroup();
                        window.add_toast(new Adw.Toast({
                            title: _(`Model ${m.name} activated.`)
                        }));
                    }
                };

                const fallbackLocalDownload = () => {
                    downloadingProgress.set(modelKey, 0);
                    renderModelList();
                    try {
                        let proc = new Gio.Subprocess({
                            argv: ['python3', `${this.path}/daemon/main.py`, '--download-only', '--provider', m.provider, '--model', m.id],
                            flags: Gio.SubprocessFlags.NONE
                        });
                        proc.init(null);
                        proc.wait_async(null, () => {
                            downloadingProgress.delete(modelKey);
                            GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
                                renderModelList();
                                if (typeof refreshCacheGroup === 'function') refreshCacheGroup();
                                return GLib.SOURCE_REMOVE;
                            });
                        });
                    } catch (e) {
                        downloadingProgress.delete(modelKey);
                        renderModelList();
                    }
                };

                const startDownload = () => {
                    window.add_toast(new Adw.Toast({
                        title: _(`Started downloading ${m.name}...`)
                    }));

                    downloadingProgress.set(modelKey, 0);
                    renderModelList();

                    try {
                        Gio.DBus.session.call(
                            'org.local.VoiceAssistant',
                            '/org/local/VoiceAssistant',
                            'org.local.VoiceAssistant',
                            'DownloadModel',
                            new GLib.Variant('(ss)', [m.provider, m.id]),
                            null,
                            Gio.DBusCallFlags.NONE,
                            -1,
                            null,
                            (conn, res) => {
                                try {
                                    conn.call_finish(res);
                                } catch (e) {
                                    fallbackLocalDownload();
                                }
                            }
                        );
                    } catch (err) {
                        fallbackLocalDownload();
                    }
                };

                row.connect('activated', () => {
                    if (isDownloading) return;
                    if (isInstalled) {
                        selectModel();
                    } else {
                        startDownload();
                    }
                });

                if (checkBtn) {
                    checkBtn.connect('toggled', () => {
                        if (checkBtn.active && isInstalled) {
                            selectModel();
                        }
                    });
                }

                if (isDownloading) {
                    const progressBar = new Gtk.ProgressBar({
                        valign: Gtk.Align.CENTER,
                        width_request: 140,
                        show_text: true,
                        fraction: Math.min(1.0, Math.max(0.0, downloadPct / 100.0)),
                        text: `${downloadPct}%`
                    });

                    downloadButtons.set(modelKey, { progressBar });

                    const cancelBtn = Gtk.Button.new_from_icon_name('process-stop-symbolic');
                    cancelBtn.valign = Gtk.Align.CENTER;
                    cancelBtn.add_css_class('flat');
                    cancelBtn.add_css_class('error');
                    cancelBtn.tooltip_text = _('Cancel download');

                    cancelBtn.connect('clicked', () => {
                        cancelBtn.sensitive = false;
                        window.add_toast(new Adw.Toast({
                            title: _(`Downloading of ${m.name} cancelled`)
                        }));
                        try {
                            Gio.DBus.session.call(
                                'org.local.VoiceAssistant',
                                '/org/local/VoiceAssistant',
                                'org.local.VoiceAssistant',
                                'CancelDownload',
                                new GLib.Variant('(ss)', [m.provider, m.id]),
                                null,
                                Gio.DBusCallFlags.NONE,
                                -1,
                                null,
                                (conn, res) => {
                                    try {
                                        conn.call_finish(res);
                                    } catch (e) { }
                                }
                            );
                        } catch (e) {
                            console.error('Errore chiamata CancelDownload:', e);
                        }
                        downloadingProgress.delete(modelKey);
                        downloadButtons.delete(modelKey);
                        GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
                            renderModelList();
                            return GLib.SOURCE_REMOVE;
                        });
                    });

                    const box = new Gtk.Box({ spacing: 6, valign: Gtk.Align.CENTER });
                    box.append(progressBar);
                    box.append(cancelBtn);
                    row.add_suffix(box);
                } else if (isInstalled) {
                    const deleteBtn = Gtk.Button.new_from_icon_name('user-trash-symbolic');
                    deleteBtn.valign = Gtk.Align.CENTER;
                    deleteBtn.add_css_class('flat');

                    let activeLang = settings.get_string('language') || 'it';
                    let isRequiredVoskModel = (m.provider === 'vosk') && (
                        (activeLang === 'it' && m.id === 'vosk-model-small-it-0.22') ||
                        (activeLang !== 'it' && m.id === 'vosk-model-small-en-us-0.15')
                    );

                    if (isCurrent) {
                        deleteBtn.sensitive = false;
                        deleteBtn.tooltip_text = _('Cannot delete currently active model');
                    } else if (isRequiredVoskModel) {
                        deleteBtn.sensitive = false;
                        deleteBtn.tooltip_text = _('Vosk model required for wakeword detection');
                    } else {
                        deleteBtn.sensitive = true;
                        deleteBtn.add_css_class('error');
                        deleteBtn.tooltip_text = _('Delete model from disk');
                    }

                    deleteBtn.connect('clicked', () => {
                        let folderName = m.provider === 'whisper' ? `whisper-${m.id}` : m.id;
                        let targetDir = currentModelsPath + '/' + folderName;
                        try {
                            let proc = new Gio.Subprocess({
                                argv: ['rm', '-rf', targetDir],
                                flags: Gio.SubprocessFlags.NONE
                            });
                            proc.init(null);
                            proc.wait_async(null, () => {
                                window.add_toast(new Adw.Toast({
                                    title: _(`Model ${m.name} deleted.`)
                                }));
                                renderModelList();
                                if (typeof refreshCacheGroup === 'function') refreshCacheGroup();
                            });
                        } catch (e) {
                            console.error('Errore eliminazione modello:', e);
                        }
                    });

                    row.add_suffix(deleteBtn);
                } else {
                    const dlBtn = Gtk.Button.new_from_icon_name('folder-download-symbolic');
                    dlBtn.valign = Gtk.Align.CENTER;
                    dlBtn.add_css_class('flat');
                    dlBtn.tooltip_text = _('Download model');
                    dlBtn.connect('clicked', startDownload);
                    row.add_suffix(dlBtn);
                }

                targetGroup.add(row);
                activeModelGroupRows.push({ row, group: targetGroup });
            });
        };

        const fetchVoskModels = () => {
            const processModelsJson = (json) => {
                if (!Array.isArray(json)) return;
                let list = json.filter(m => {
                    let isObsolete = (m.obsolete === 'true' || m.obsolete === true);
                    return !isObsolete;
                }).map(m => {
                    let mId = m.id || m.name;
                    let sizeStr = m.size_text || m.size || '';
                    let langStr = m.lang_text || m.lang || '';
                    let sub = `Vosk • ${sizeStr} • ${langStr}`;
                    let fullName = m.name || `${langStr} - ${mId}`;
                    return {
                        id: mId,
                        provider: 'vosk',
                        name: fullName,
                        subtitle: sub,
                        lang: m.lang || '',
                        lang_text: langStr,
                        size_text: sizeStr,
                        url: m.url
                    };
                });

                if (list.length > 0) {
                    fetchedVoskModels = list;
                    renderModelList();
                }
            };

            const fetchVoskModelsOnline = () => {
                try {
                    let message = Soup.Message.new('GET', 'https://alphacephei.com/vosk/models/model-list.json');
                    let session = new Soup.Session();
                    session.send_and_read_async(message, GLib.PRIORITY_DEFAULT, null, (sess, res) => {
                        try {
                            let bytes = session.send_and_read_finish(res);
                            if (bytes) {
                                let text = new TextDecoder().decode(bytes.get_data());
                                let json = JSON.parse(text);
                                processModelsJson(json);
                            }
                        } catch (err) {
                            console.error('[VoiceAssistant] Errore parsing modelli Vosk online:', err);
                        }
                    });
                } catch (err) {
                    console.error('[VoiceAssistant] Errore fetch HTTP modelli Vosk:', err);
                }
            };

            try {
                Gio.DBus.session.call(
                    'org.local.VoiceAssistant',
                    '/org/local/VoiceAssistant',
                    'org.local.VoiceAssistant',
                    'GetAvailableModels',
                    new GLib.Variant('(s)', ['vosk']),
                    null,
                    Gio.DBusCallFlags.NONE,
                    -1,
                    null,
                    (conn, res) => {
                        try {
                            let reply = conn.call_finish(res);
                            let [jsonStr] = reply.recursiveUnpack();
                            let json = JSON.parse(jsonStr);
                            processModelsJson(json);
                        } catch (e) {
                            fetchVoskModelsOnline();
                        }
                    }
                );
            } catch (err) {
                fetchVoskModelsOnline();
            }
        };

        fetchVoskModels();


        // ==========================================
        // 3. INTELLIGENZA ARTIFICIALE (LLM) BINDINGS
        // ==========================================
        const llmEnableRow = builder.get_object('llm_enable_row');
        const llmModeRow = builder.get_object('llm_mode_row');
        const llmSystemPromptRow = builder.get_object('llm_system_prompt_row');
        const llmUrlRow = builder.get_object('llm_url_row');
        const llmModelRow = builder.get_object('llm_model_row');

        settings.bind('llm-enabled', llmEnableRow, 'active', Gio.SettingsBindFlags.DEFAULT);
        settings.bind('llm-system-prompt', llmSystemPromptRow, 'text', Gio.SettingsBindFlags.DEFAULT);
        settings.bind('llm-url', llmUrlRow, 'text', Gio.SettingsBindFlags.DEFAULT);
        settings.bind('llm-model', llmModelRow, 'text', Gio.SettingsBindFlags.DEFAULT);

        let currentLlmMode = settings.get_string('llm-mode') || 'local';
        llmModeRow.selected = (currentLlmMode === 'ollama' || currentLlmMode === 'http') ? 1 : 0;

        const updateLlmModeVisibility = () => {
            let isExternal = (llmModeRow.selected === 1);
            llmUrlRow.visible = isExternal;
            llmModelRow.visible = isExternal;
        };
        updateLlmModeVisibility();

        llmModeRow.connect('notify::selected', () => {
            let newMode = (llmModeRow.selected === 1) ? 'ollama' : 'local';
            settings.set_string('llm-mode', newMode);
            updateLlmModeVisibility();
        });


        // ==========================================
        // 4. SINTESI VOCALE (TTS) BINDINGS
        // ==========================================
        const ttsEnableRow = builder.get_object('tts_enable_row');
        const ttsEngineRow = builder.get_object('tts_engine_row');

        settings.bind('tts-enabled', ttsEnableRow, 'active', Gio.SettingsBindFlags.DEFAULT);

        let currentTts = settings.get_string('tts-engine') || 'piper';
        ttsEngineRow.selected = (currentTts === 'espeak') ? 1 : 0;
        ttsEngineRow.connect('notify::selected', () => {
            let newEngine = (ttsEngineRow.selected === 1) ? 'espeak' : 'piper';
            settings.set_string('tts-engine', newEngine);
        });


        // ==========================================
        // 5. ARCHIVIAZIONE E GESTIONE CACHE
        // ==========================================
        const modelsPathRow = builder.get_object('models_path_row');
        const choosePathBtn = builder.get_object('choose_path_btn');
        const resetPathBtn = builder.get_object('reset_path_btn');
        const cacheGroup = builder.get_object('cache_group');
        const cleanUnusedBtn = builder.get_object('clean_unused_btn');

        const updateModelsPathDisplay = () => {
            const currentPath = getModelsPath();
            modelsPathRow.subtitle = formatPathForDisplay(currentPath);
        };
        updateModelsPathDisplay();

        choosePathBtn.connect('clicked', () => {
            let chooser = new Gtk.FileChooserNative({
                title: _('Select models directory'),
                action: Gtk.FileChooserAction.SELECT_FOLDER,
                transient_for: window,
                modal: true
            });

            chooser.connect('response', (dialog, response_id) => {
                if (response_id === Gtk.ResponseType.ACCEPT) {
                    let folder = chooser.get_file();
                    if (folder) {
                        let newPath = folder.get_path();
                        settings.set_string('models-dir', newPath);
                        updateModelsPathDisplay();
                        refreshCacheGroup();
                        renderModelList();
                    }
                }
                chooser.destroy();
            });
            chooser.show();
        });

        resetPathBtn.connect('clicked', () => {
            settings.reset('models-dir');
            updateModelsPathDisplay();
            refreshCacheGroup();
            renderModelList();
        });

        cleanUnusedBtn.connect('clicked', () => {
            const currentModelsPath = getModelsPath();
            let currentModel = settings.get_string('stt-model') || '';
            let currentProvider = settings.get_string('stt-provider') || 'vosk';
            let activeFolderName = currentProvider === 'whisper' ? `whisper-${currentModel}` : currentModel;

            const modelsDir = Gio.File.new_for_path(currentModelsPath);
            let countRemoved = 0;
            try {
                let enumerator = modelsDir.enumerate_children('standard::name,standard::type', Gio.FileQueryInfoFlags.NONE, null);
                let info;
                while ((info = enumerator.next_file(null)) !== null) {
                    if (info.get_file_type() === Gio.FileType.DIRECTORY) {
                        let name = info.get_name();
                        let activeLang = settings.get_string('language') || 'it';
                        let isRequiredVosk = (activeLang === 'it' && name === 'vosk-model-small-it-0.22') ||
                            (activeLang !== 'it' && name === 'vosk-model-small-en-us-0.15');

                        if (name !== activeFolderName && !isRequiredVosk && !name.startsWith('.')) {
                            let targetDir = currentModelsPath + '/' + name;
                            let proc = new Gio.Subprocess({
                                argv: ['rm', '-rf', targetDir],
                                flags: Gio.SubprocessFlags.NONE
                            });
                            proc.init(null);
                            countRemoved++;
                        }
                    }
                }
            } catch (e) { }

            refreshCacheGroup();
            renderModelList();
            window.add_toast(new Adw.Toast({
                title: countRemoved > 0
                    ? _('Unused models removed successfully!')
                    : _('No unused models to remove.')
            }));
        });

        let activeCacheRows = [];

        refreshCacheGroup = () => {
            for (const r of activeCacheRows) {
                try {
                    cacheGroup.remove(r);
                } catch (e) { }
            }
            activeCacheRows = [];

            const currentModelsPath = getModelsPath();
            cacheGroup.set_description(_(`Manage downloaded models in ${formatPathForDisplay(currentModelsPath)}`));

            let currentModel = settings.get_string('stt-model') || '';
            let currentProvider = settings.get_string('stt-provider') || 'vosk';
            let activeFolderName = currentProvider === 'whisper' ? `whisper-${currentModel}` : currentModel;

            const modelsDir = Gio.File.new_for_path(currentModelsPath);
            let downloadedModels = [];
            try {
                let enumerator = modelsDir.enumerate_children('standard::name,standard::type', Gio.FileQueryInfoFlags.NONE, null);
                let info;
                while ((info = enumerator.next_file(null)) !== null) {
                    if (info.get_file_type() === Gio.FileType.DIRECTORY) {
                        let name = info.get_name();
                        if (!name.startsWith('.')) {
                            downloadedModels.push(name);
                        }
                    }
                }
            } catch (e) { }

            let unusedCount = downloadedModels.filter(m => m !== activeFolderName).length;
            cleanUnusedBtn.sensitive = (unusedCount > 0);

            if (downloadedModels.length === 0) {
                const emptyRow = new Adw.ActionRow({
                    title: _('No downloaded models'),
                    subtitle: _('Models will be downloaded automatically on first use')
                });
                cacheGroup.add(emptyRow);
                activeCacheRows.push(emptyRow);
            } else {
                downloadedModels.sort();
                for (const modelName of downloadedModels) {
                    const fullPath = currentModelsPath + '/' + modelName;
                    const size = getDirSize(fullPath);
                    let isActive = (modelName === activeFolderName);

                    const row = new Adw.ActionRow({
                        title: modelName,
                        subtitle: size
                    });

                    if (isActive) {
                        const activeIcon = Gtk.Image.new_from_icon_name('check-plain-symbolic');
                        activeIcon.valign = Gtk.Align.CENTER;
                        activeIcon.add_css_class('accent');
                        activeIcon.tooltip_text = _('Currently active model');
                        row.add_suffix(activeIcon);
                    }

                    cacheGroup.add(row);
                    activeCacheRows.push(row);
                }
            }
        };

        refreshCacheGroup();


        // ==========================================
        // 6. INFORMAZIONI BINDINGS
        // ==========================================
        const docBtn = builder.get_object('doc_btn');
        docBtn.connect('clicked', () => {
            Gio.AppInfo.launch_default_for_uri('https://github.com/Scroker/voice-assistant', null);
        });


        // ==========================================
        // 7. SIDEBAR E GERARCHIA DI NAVIGAZIONE
        // ==========================================
        const sidebarListBox = builder.get_object('sidebar_list_box');
        const contentNavigationView = builder.get_object('content_navigation_view');
        const mainContentNavPage = builder.get_object('main_content_nav_page');
        const contentTitle = builder.get_object('content_title');
        const stack = builder.get_object('stack');

        const pages = [
            { id: 'general_page', page: builder.get_object('general_page'), title: _('General'), row: builder.get_object('row_general') },
            { id: 'stt_page', page: builder.get_object('stt_page'), title: _('Speech Engine (STT)'), row: builder.get_object('row_stt') },
            { id: 'llm_page', page: builder.get_object('llm_page'), title: _('Artificial Intelligence (LLM)'), row: builder.get_object('row_llm') },
            { id: 'tts_page', page: builder.get_object('tts_page'), title: _('Text-to-Speech (TTS)'), row: builder.get_object('row_tts') },
            { id: 'mcp_page', page: builder.get_object('mcp_page'), title: _('Tools (MCP)'), row: builder.get_object('row_mcp') },
            { id: 'models_page', page: builder.get_object('models_page'), title: _('Storage and Models'), row: builder.get_object('row_models') },
            { id: 'about_page', page: builder.get_object('about_page'), title: _('About'), row: builder.get_object('row_about') }
        ];

        const openSelector = () => {
            queryDownloadingModels(() => {
                renderModelList();
            });
            contentNavigationView.push(modelSelectorPage);
            splitView.set_show_content(true);
        };

        currentModelRow.connect('activated', openSelector);
        openModelSelectorBtn.connect('clicked', openSelector);

        const selectSidebarPage = (row) => {
            if (!row) return;
            const target = pages.find(p => p.row === row);
            if (target) {
                if (target.id === 'stt_page') {
                    queryDownloadingModels(() => {
                        renderModelList();
                    });
                }

                if (contentNavigationView.get_visible_page() === modelSelectorPage) {
                    contentNavigationView.pop();
                }

                if (target.page) {
                    stack.set_visible_child(target.page);
                } else {
                    stack.set_visible_child_name(target.id);
                }

                contentTitle.set_title(target.title);
                mainContentNavPage.set_title(target.title);

                splitView.set_show_content(true);
            }
        };

        sidebarListBox.connect('row-activated', (listbox, row) => selectSidebarPage(row));
        sidebarListBox.connect('row-selected', (listbox, row) => selectSidebarPage(row));

        // Seleziona la prima riga di default
        sidebarListBox.select_row(pages[0].row);
        selectSidebarPage(pages[0].row);

        // Imposta Adw.NavigationSplitView come contenuto root della finestra
        window.set_content(splitView);
    }
}
