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
import { ExtensionPreferences, gettext as _ } from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

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

        const settings = this.getSettings('org.gnome.shell.extensions.voice-assistant');

        // Dimensioni di default per desktop
        window.set_default_size(860, 600);

        // Caricamento interfaccia dal file Blueprint (compilato in prefs.ui nelle risorse)
        const builder = Gtk.Builder.new_from_resource('/org/gnome/shell/extensions/voice-assistant/ui/prefs.ui');
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
        let pendingProvider = activeProvider;
        let pendingModel = activeModel;

        const applyBtn = builder.get_object('apply_btn');
        const selectorApplyBtn = builder.get_object('selector_apply_btn');
        const applyButtons = [applyBtn, selectorApplyBtn];

        const updateApplyButtons = () => {
            let hasChanges = (pendingProvider !== activeProvider || pendingModel !== activeModel);
            for (const btn of applyButtons) {
                if (btn) btn.sensitive = hasChanges;
            }
        };
        updateApplyButtons();

        const handleApplyChanges = () => {
            if (pendingProvider !== activeProvider || pendingModel !== activeModel) {
                settings.set_string('stt-provider', pendingProvider);
                settings.set_string('stt-model', pendingModel);
                activeProvider = pendingProvider;
                activeModel = pendingModel;

                updateActiveModelSubtitle();

                try {
                    let cmd = ['systemctl', '--user', 'restart', 'voice-assistant.service'];
                    let proc = new Gio.Subprocess({ argv: cmd, flags: Gio.SubprocessFlags.NONE });
                    proc.init(null);
                } catch (e) { }

                updateApplyButtons();
                renderModelList();
                if (typeof refreshCacheGroup === 'function') refreshCacheGroup();

                window.add_toast(new Adw.Toast({
                    title: _('Modifiche applicate con successo!')
                }));
            }
        };

        applyBtn.connect('clicked', handleApplyChanges);
        selectorApplyBtn.connect('clicked', handleApplyChanges);

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
            { id: 'tiny', provider: 'whisper', name: 'Whisper Tiny', subtitle: 'Whisper • ~75MB • Multilingua', lang: 'multilingual', lang_text: 'Multilingual', size_text: '~75MB' },
            { id: 'tiny.en', provider: 'whisper', name: 'Whisper Tiny (English)', subtitle: 'Whisper • ~75MB • Solo Inglese', lang: 'en', lang_text: 'English', size_text: '~75MB' },
            { id: 'base', provider: 'whisper', name: 'Whisper Base (Consigliato)', subtitle: 'Whisper • ~140MB • Multilingua', lang: 'multilingual', lang_text: 'Multilingual', size_text: '~140MB' },
            { id: 'base.en', provider: 'whisper', name: 'Whisper Base (English)', subtitle: 'Whisper • ~140MB • Solo Inglese', lang: 'en', lang_text: 'English', size_text: '~140MB' },
            { id: 'small', provider: 'whisper', name: 'Whisper Small', subtitle: 'Whisper • ~466MB • Multilingua', lang: 'multilingual', lang_text: 'Multilingual', size_text: '~466MB' },
            { id: 'small.en', provider: 'whisper', name: 'Whisper Small (English)', subtitle: 'Whisper • ~466MB • Solo Inglese', lang: 'en', lang_text: 'English', size_text: '~466MB' },
            { id: 'medium', provider: 'whisper', name: 'Whisper Medium', subtitle: 'Whisper • ~1.5GB • Multilingua', lang: 'multilingual', lang_text: 'Multilingual', size_text: '~1.5GB' },
            { id: 'medium.en', provider: 'whisper', name: 'Whisper Medium (English)', subtitle: 'Whisper • ~1.5GB • Solo Inglese', lang: 'en', lang_text: 'English', size_text: '~1.5GB' },
            { id: 'large-v3', provider: 'whisper', name: 'Whisper Large v3', subtitle: 'Whisper • ~3.1GB • Multilingua', lang: 'multilingual', lang_text: 'Multilingual', size_text: '~3.1GB' }
        ];

        let fetchedVoskModels = [
            { id: 'vosk-model-small-it-0.22', provider: 'vosk', name: 'Italian - vosk-model-small-it-0.22', subtitle: 'Vosk • 47.4MiB • Italian', lang: 'it', lang_text: 'Italian', size_text: '47.4MiB', url: 'https://alphacephei.com/vosk/models/vosk-model-small-it-0.22.zip' },
            { id: 'vosk-model-it-0.22', provider: 'vosk', name: 'Italian - vosk-model-it-0.22', subtitle: 'Vosk • 1.2GiB • Italian', lang: 'it', lang_text: 'Italian', size_text: '1.2GiB', url: 'https://alphacephei.com/vosk/models/vosk-model-it-0.22.zip' },
            { id: 'vosk-model-small-en-us-0.15', provider: 'vosk', name: 'English - vosk-model-small-en-us-0.15', subtitle: 'Vosk • 40MiB • English', lang: 'en', lang_text: 'English', size_text: '40MiB', url: 'https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip' },
            { id: 'vosk-model-en-us-0.22', provider: 'vosk', name: 'English - vosk-model-en-us-0.22', subtitle: 'Vosk • 1.8GiB • English', lang: 'en', lang_text: 'English', size_text: '1.8GiB', url: 'https://alphacephei.com/vosk/models/vosk-model-en-us-0.22.zip' }
        ];

        const searchEntry = builder.get_object('search_entry');
        const filterToggleBtn = builder.get_object('filter_toggle_btn');
        const filterBox = builder.get_object('filter_box');
        const filterBtnAll = builder.get_object('filter_btn_all');
        const filterBtnInstalled = builder.get_object('filter_btn_installed');
        const filterBtnDownloading = builder.get_object('filter_btn_downloading');
        const modelsGroupContainer = builder.get_object('models_group_container');
        const modelSelectorPage = builder.get_object('model_selector_page');

        filterToggleBtn.connect('toggled', () => {
            filterBox.visible = filterToggleBtn.active;
        });

        let activeFilter = 'all';
        const setFilter = (fId) => {
            activeFilter = fId;
            renderModelList();
        };

        filterBtnAll.connect('toggled', () => { if (filterBtnAll.active) setFilter('all'); });
        filterBtnInstalled.connect('toggled', () => { if (filterBtnInstalled.active) setFilter('installed'); });
        filterBtnDownloading.connect('toggled', () => { if (filterBtnDownloading.active) setFilter('downloading'); });

        searchEntry.connect('search-changed', () => renderModelList());

        let activeModelGroupRows = [];

        renderModelList = () => {
            for (const r of activeModelGroupRows) {
                try {
                    modelsGroupContainer.remove(r);
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

            const filteredModels = allModels.filter(m => {
                let modelKey = `${m.provider}:${m.id}`;
                let isDownloading = downloadingProgress.has(modelKey);
                let isInstalled = !isDownloading && (installedSet.has(m.id) || installedSet.has(`${m.provider}-${m.id}`) || (m.provider === 'whisper' && installedSet.has(`whisper-${m.id}`)));

                if (activeFilter === 'installed' && !isInstalled) return false;
                if (activeFilter === 'downloading' && !isDownloading) return false;

                if (query.length > 0) {
                    let text = `${m.name} ${m.id} ${m.provider} ${m.lang} ${m.lang_text} ${m.size_text}`.toLowerCase();
                    if (!text.includes(query)) return false;
                }
                return true;
            });

            if (filteredModels.length === 0) {
                const emptyRow = new Adw.ActionRow({
                    title: _('Nessun modello trovato'),
                    subtitle: _('Prova a modificare i filtri o il termine di ricerca.')
                });
                modelsGroupContainer.add(emptyRow);
                activeModelGroupRows.push(emptyRow);
                return;
            }

            const modelGroupLeader = new Gtk.CheckButton();

            filteredModels.forEach(m => {
                let isCurrent = (pendingProvider === m.provider && pendingModel === m.id);
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
                    pendingProvider = m.provider;
                    pendingModel = m.id;
                    updateApplyButtons();
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
                        title: _(`Avviato scaricamento di ${m.name}...`)
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
                    cancelBtn.tooltip_text = _('Annulla scaricamento');

                    cancelBtn.connect('clicked', () => {
                        cancelBtn.sensitive = false;
                        window.add_toast(new Adw.Toast({
                            title: _(`Scaricamento di ${m.name} annullato`)
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
                        deleteBtn.tooltip_text = _('Impossibile eliminare il modello attualmente in uso');
                    } else if (isRequiredVoskModel) {
                        deleteBtn.sensitive = false;
                        deleteBtn.tooltip_text = _('Modello Vosk necessario per il rilevamento della wakeword');
                    } else {
                        deleteBtn.sensitive = true;
                        deleteBtn.add_css_class('error');
                        deleteBtn.tooltip_text = _('Elimina modello dal disco');
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
                                    title: _(`Modello ${m.name} eliminato.`)
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
                    dlBtn.tooltip_text = _('Scarica modello');
                    dlBtn.connect('clicked', startDownload);
                    row.add_suffix(dlBtn);
                }

                modelsGroupContainer.add(row);
                activeModelGroupRows.push(row);
            });
        };

        const fetchVoskModels = () => {
            try {
                let message = Soup.Message.new('GET', 'https://alphacephei.com/vosk/models/model-list.json');
                let session = new Soup.Session();
                session.send_and_read_async(message, GLib.PRIORITY_DEFAULT, null, (sess, res) => {
                    try {
                        let bytes = session.send_and_read_finish(res);
                        if (bytes) {
                            let text = new TextDecoder().decode(bytes.get_data());
                            let json = JSON.parse(text);
                            if (Array.isArray(json)) {
                                fetchedVoskModels = json
                                    .filter(m => m.lang === 'it' || m.lang === 'en' || m.lang === 'en-us')
                                    .map(m => ({
                                        id: m.name,
                                        provider: 'vosk',
                                        name: `${m.lang_text || m.lang} - ${m.name}`,
                                        subtitle: `Vosk • ${m.size_text || ''} • ${m.lang_text || m.lang}`,
                                        lang: m.lang,
                                        lang_text: m.lang_text,
                                        size_text: m.size_text,
                                        url: m.url
                                    }));
                                renderModelList();
                            }
                        }
                    } catch (err) { }
                });
            } catch (err) { }
        };

        fetchVoskModels();


        // ==========================================
        // 3. INTELLIGENZA ARTIFICIALE (LLM) BINDINGS
        // ==========================================
        const llmEnableRow = builder.get_object('llm_enable_row');
        const llmUrlRow = builder.get_object('llm_url_row');
        const llmModelRow = builder.get_object('llm_model_row');

        settings.bind('llm-enabled', llmEnableRow, 'active', Gio.SettingsBindFlags.DEFAULT);
        settings.bind('llm-url', llmUrlRow, 'text', Gio.SettingsBindFlags.DEFAULT);
        settings.bind('llm-model', llmModelRow, 'text', Gio.SettingsBindFlags.DEFAULT);


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
                title: _('Seleziona cartella modelli'),
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
                    ? _('Modelli inutilizzati eliminati con successo!')
                    : _('Nessun modello inutilizzato da eliminare.')
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
            cacheGroup.set_description(_(`Gestisci i modelli scaricati in ${formatPathForDisplay(currentModelsPath)}`));

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
                    title: _('Nessun modello scaricato'),
                    subtitle: _('I modelli verranno scaricati automaticamente al primo utilizzo')
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
                        activeIcon.tooltip_text = _('Modello attualmente in uso');
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
            Gio.AppInfo.launch_default_for_uri('https://github.com/mkswap/voice-assistant', null);
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
            { id: 'general', title: _('Generali'), row: builder.get_object('row_general') },
            { id: 'stt', title: _('Motore Vocale (STT)'), row: builder.get_object('row_stt') },
            { id: 'llm', title: _('Intelligenza Artificiale (LLM)'), row: builder.get_object('row_llm') },
            { id: 'tts', title: _('Sintesi Vocale (TTS)'), row: builder.get_object('row_tts') },
            { id: 'mcp', title: _('Strumenti (MCP)'), row: builder.get_object('row_mcp') },
            { id: 'models', title: _('Archiviazione e Modelli'), row: builder.get_object('row_models') },
            { id: 'about', title: _('Informazioni'), row: builder.get_object('row_about') }
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
                if (target.id === 'stt') {
                    queryDownloadingModels(() => {
                        renderModelList();
                    });
                }

                if (contentNavigationView.get_visible_page() === modelSelectorPage) {
                    contentNavigationView.pop();
                }

                stack.set_visible_child_name(target.id);
                contentTitle.set_title(target.title);
                mainContentNavPage.set_title(target.title);

                splitView.set_show_content(true);
            }
        };

        sidebarListBox.connect('row-activated', (listbox, row) => selectSidebarPage(row));

        // Seleziona la prima riga di default
        sidebarListBox.select_row(pages[0].row);
        selectSidebarPage(pages[0].row);

        // Imposta Adw.NavigationSplitView come contenuto root della finestra
        window.set_content(splitView);
    }
}
