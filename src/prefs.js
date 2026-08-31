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

        // Helper per il percorso dei modelli
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
            } catch (e) {}
            return '?';
        };

        let refreshCacheGroup = () => {};

        // ==========================================
        // 1. PAGINA GENERALI
        // ==========================================
        const generalPage = new Adw.PreferencesPage();
        const generalGroup = new Adw.PreferencesGroup({
            title: _('Assistente Vocale'),
            description: _('Configurazioni generali e attivazione dell\'assistente.')
        });
        generalPage.add(generalGroup);

        const enableSwitchRow = new Adw.SwitchRow({
            title: _('Abilita Assistente Vocale'),
            subtitle: _('Attiva o disattiva l\'ascolto in background'),
            active: settings.get_boolean('enabled')
        });
        settings.bind('enabled', enableSwitchRow, 'active', Gio.SettingsBindFlags.DEFAULT);
        generalGroup.add(enableSwitchRow);

        const wakewordRow = new Adw.EntryRow({
            title: _('Wakeword'),
            text: settings.get_string('wakeword')
        });
        settings.bind('wakeword', wakewordRow, 'text', Gio.SettingsBindFlags.DEFAULT);
        generalGroup.add(wakewordRow);

        // ==========================================
        // 2. PAGINA MOTORE VOCALE (STT) E SELETTORE MODELLI
        // ==========================================
        // State tracking per modifiche in sospeso
        let activeProvider = settings.get_string('stt-provider') || 'vosk';
        let activeModel = settings.get_string('stt-model') || 'vosk-model-small-it-0.22';
        let pendingProvider = activeProvider;
        let pendingModel = activeModel;

        const applyButtons = [];
        const updateApplyButtons = () => {
            let hasChanges = (pendingProvider !== activeProvider || pendingModel !== activeModel);
            for (const btn of applyButtons) {
                btn.sensitive = hasChanges;
            }
        };

        const downloadingProgress = new Map();
        const downloadButtons = new Map();
        let renderModelList = () => {};

        // Query initial downloading models from daemon
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
                        for (let k in obj) {
                            downloadingProgress.set(k, obj[k]);
                        }
                        if (typeof renderModelList === 'function') renderModelList();
                    } catch (e) {}
                }
            );
        } catch (e) {}

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
        } catch (e) {}

        const sttPage = new Adw.PreferencesPage();
        const sttMainGroup = new Adw.PreferencesGroup({
            title: _('Motore di Riconoscimento Vocale (STT)'),
            description: _('Configura il motore e il modello per la trascrizione del parlato in testo.')
        });
        sttPage.add(sttMainGroup);

        const currentModelRow = new Adw.ActionRow({
            title: _('Modello STT In Uso'),
            subtitle: _('Caricamento informazioni...'),
            activatable: true
        });
        const sttMicIcon = new Gtk.Image({
            icon_name: 'audio-input-microphone-symbolic',
            margin_end: 12
        });
        currentModelRow.add_prefix(sttMicIcon);

        const openModelSelectorBtn = Gtk.Button.new_from_icon_name('go-next-symbolic');
        openModelSelectorBtn.valign = Gtk.Align.CENTER;
        openModelSelectorBtn.add_css_class('flat');
        currentModelRow.add_suffix(openModelSelectorBtn);
        sttMainGroup.add(currentModelRow);

        // Gruppo Accelerazione Hardware (Whisper)
        const whisperHardwareGroup = new Adw.PreferencesGroup({
            title: _('Accelerazione Hardware (Whisper)'),
            description: _('Configura il dispositivo di calcolo per l\'esecuzione di Whisper.')
        });
        sttPage.add(whisperHardwareGroup);

        const hwOptions = [
            { id: 'cpu', title: _('CPU (Compatibile con tutti i sistemi)'), subtitle: _('Esecuzione standard tramite processore') },
            { id: 'cuda', title: _('CUDA (GPU Nvidia)'), subtitle: _('Accelerazione hardware tramite scheda video Nvidia') }
        ];

        let currentHw = settings.get_string('stt-hardware') || 'cpu';
        let hwFirstRadio = null;

        hwOptions.forEach((opt) => {
            const row = new Adw.ActionRow({
                title: opt.title,
                subtitle: opt.subtitle,
                activatable: true
            });
            const checkBtn = new Gtk.CheckButton({
                valign: Gtk.Align.CENTER,
                margin_end: 12
            });
            if (!hwFirstRadio) {
                hwFirstRadio = checkBtn;
            } else {
                checkBtn.set_group(hwFirstRadio);
            }
            if (opt.id === currentHw) {
                checkBtn.active = true;
            }

            row.add_prefix(checkBtn);

            const selectHw = () => {
                checkBtn.active = true;
                settings.set_string('stt-hardware', opt.id);
            };

            row.connect('activated', selectHw);
            checkBtn.connect('toggled', () => {
                if (checkBtn.active) settings.set_string('stt-hardware', opt.id);
            });

            whisperHardwareGroup.add(row);
        });

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
        // PREDISPOSIZIONE SELETTORE MODELLI (NAVIGATION PAGE)
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

        const selectorToolbarView = new Adw.ToolbarView();
        const selectorHeaderBar = new Adw.HeaderBar({
            title_widget: new Adw.WindowTitle({
                title: _('Seleziona Modello STT')
            })
        });
        selectorToolbarView.add_top_bar(selectorHeaderBar);

        const selectorContentBox = new Gtk.Box({
            orientation: Gtk.Orientation.VERTICAL,
            spacing: 12,
            margin_top: 12,
            margin_bottom: 12,
            margin_start: 12,
            margin_end: 12
        });

        // Search Entry & Filter Toggle Button
        const searchBox = new Gtk.Box({
            orientation: Gtk.Orientation.HORIZONTAL,
            spacing: 6
        });

        const searchEntry = new Gtk.SearchEntry({
            placeholder_text: _('Cerca per nome, lingua (it, en), dimensione o provider...'),
            hexpand: true
        });

        const filterToggleBtn = new Gtk.ToggleButton({
            icon_name: 'filter-symbolic',
            tooltip_text: _('Mostra/Nascondi Filtri'),
            valign: Gtk.Align.CENTER
        });

        searchBox.append(searchEntry);
        searchBox.append(filterToggleBtn);

        const clampSearch = new Adw.Clamp({
            maximum_size: 600,
            child: searchBox
        });
        selectorContentBox.append(clampSearch);

        // ToggleGroup per i filtri STT (Tutti | Vosk | Whisper | Installati)
        const filterBox = new Gtk.Box({
            orientation: Gtk.Orientation.HORIZONTAL,
            spacing: 0,
            halign: Gtk.Align.CENTER,
            margin_top: 4,
            margin_bottom: 8,
            visible: false
        });
        filterBox.add_css_class('linked');

        filterToggleBtn.connect('toggled', () => {
            filterBox.visible = filterToggleBtn.active;
        });

        let activeFilter = 'all';
        let firstToggle = null;
        const filters = [
            { id: 'all', label: _('Tutti') },
            { id: 'vosk', label: _('Vosk') },
            { id: 'whisper', label: _('Whisper') },
            { id: 'installed', label: _('Installati') }
        ];

        filters.forEach(f => {
            const btn = new Gtk.ToggleButton({
                label: f.label,
                active: (f.id === activeFilter)
            });

            if (!firstToggle) {
                firstToggle = btn;
            } else {
                btn.set_group(firstToggle);
            }

            btn.connect('toggled', () => {
                if (btn.active) {
                    activeFilter = f.id;
                    renderModelList();
                }
            });

            filterBox.append(btn);
        });
        selectorContentBox.append(filterBox);

        // Container del gruppo dei modelli
        const modelsGroupContainer = new Adw.PreferencesGroup({
            title: _('Modelli Disponibili')
        });
        const clampGroup = new Adw.Clamp({
            maximum_size: 800,
            child: modelsGroupContainer
        });
        selectorContentBox.append(clampGroup);

        const selectorScroll = new Gtk.ScrolledWindow({
            hscrollbar_policy: Gtk.PolicyType.NEVER,
            vexpand: true
        });
        selectorScroll.set_child(selectorContentBox);
        selectorToolbarView.set_content(selectorScroll);

        const modelSelectorPage = new Adw.NavigationPage({
            child: selectorToolbarView,
            title: _('Seleziona Modello STT')
        });

        const getInstalledModelIds = () => {
            const installed = new Set();
            const modelsPath = getModelsPath();
            const dir = Gio.File.new_for_path(modelsPath);
            try {
                if (dir.query_exists(null)) {
                    const enumerator = dir.enumerate_children('standard::name,standard::type', Gio.FileQueryInfoFlags.NONE, null);
                    let info;
                    while ((info = enumerator.next_file(null)) !== null) {
                        if (info.get_file_type() === Gio.FileType.DIRECTORY) {
                            let fname = info.get_name();
                            if (fname.startsWith('.')) continue;
                            installed.add(fname);
                            if (fname.startsWith('whisper-')) {
                                installed.add(fname.substring('whisper-'.length));
                            }
                        }
                    }
                }
            } catch (e) {}
            return installed;
        };

        let activeModelGroupRows = [];
        renderModelList = () => {
            downloadButtons.clear();
            for (const r of activeModelGroupRows) {
                try {
                    modelsGroupContainer.remove(r);
                } catch (e) {}
            }
            activeModelGroupRows = [];

            const installedSet = getInstalledModelIds();
            const currentProvider = pendingProvider;
            const currentModel = pendingModel;

            const allModels = [...whisperStaticModels, ...fetchedVoskModels];
            const query = searchEntry.text.trim().toLowerCase();

            const filteredModels = allModels.filter(m => {
                // Filtro categoria
                if (activeFilter === 'vosk' && m.provider !== 'vosk') return false;
                if (activeFilter === 'whisper' && m.provider !== 'whisper') return false;
                
                let isInstalled = installedSet.has(m.id) || installedSet.has(`${m.provider}-${m.id}`) || (m.provider === 'whisper' && installedSet.has(`whisper-${m.id}`));
                if (activeFilter === 'installed' && !isInstalled) return false;

                // Filtro testo di ricerca
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

            let modelFirstRadio = null;

            filteredModels.forEach(m => {
                let isCurrent = (currentProvider === m.provider && currentModel === m.id);
                let modelKey = `${m.provider}:${m.id}`;
                let isDownloading = downloadingProgress.has(modelKey);
                let downloadPct = downloadingProgress.get(modelKey) || 0;
                let isInstalled = !isDownloading && (installedSet.has(m.id) || installedSet.has(`${m.provider}-${m.id}`) || (m.provider === 'whisper' && installedSet.has(`whisper-${m.id}`)));

                const row = new Adw.ActionRow({
                    title: m.name,
                    subtitle: m.subtitle || `${m.provider.toUpperCase()} • ${m.size_text || ''}`,
                    activatable: true,
                    margin_top: 4,
                    margin_bottom: 4
                });

                const checkBtn = new Gtk.CheckButton({
                    valign: Gtk.Align.CENTER,
                    margin_end: 12,
                    sensitive: isInstalled
                });

                if (!modelFirstRadio) {
                    modelFirstRadio = checkBtn;
                } else {
                    checkBtn.set_group(modelFirstRadio);
                }

                if (isCurrent) {
                    checkBtn.active = true;
                }

                row.add_prefix(checkBtn);

                const selectModel = () => {
                    checkBtn.active = true;
                    pendingProvider = m.provider;
                    pendingModel = m.id;
                    updateApplyButtons();
                };

                const startDownload = () => {
                    downloadingProgress.set(modelKey, 0);
                    GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
                        renderModelList();
                        return GLib.SOURCE_REMOVE;
                    });

                    const fallbackLocalDownload = () => {
                        window.add_toast(new Adw.Toast({
                            title: _(`Avviato download locale di ${m.name}...`)
                        }));
                        if (m.provider === 'vosk') {
                            let url = m.url || `https://alphacephei.com/vosk/models/${m.id}.zip`;
                            let targetModelsPath = getModelsPath();
                            GLib.mkdir_with_parents(targetModelsPath, 0o755);
                            let zipPath = `${targetModelsPath}/${m.id}.zip`;
                            let cmd = ['sh', '-c', `mkdir -p "${targetModelsPath}" && wget -qO "${zipPath}" "${url}" && unzip -o "${zipPath}" -d "${targetModelsPath}/" && rm "${zipPath}"`];

                            try {
                                let proc = new Gio.Subprocess({ argv: cmd, flags: Gio.SubprocessFlags.NONE });
                                proc.init(null);
                                proc.wait_check_async(null, (p, res) => {
                                    try {
                                        p.wait_check_finish(res);
                                    } catch (err) {}
                                    downloadingProgress.delete(modelKey);
                                    GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
                                        renderModelList();
                                        if (typeof refreshCacheGroup === 'function') refreshCacheGroup();
                                        return GLib.SOURCE_REMOVE;
                                    });
                                });
                            } catch (e) {
                                downloadingProgress.delete(modelKey);
                                GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
                                    renderModelList();
                                    return GLib.SOURCE_REMOVE;
                                });
                            }
                        } else {
                            downloadingProgress.delete(modelKey);
                            GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
                                renderModelList();
                                return GLib.SOURCE_REMOVE;
                            });
                        }
                    };

                    try {
                        Gio.DBus.session.call(
                            'org.local.VoiceAssistant',
                            '/org/local/VoiceAssistant',
                            'org.local.VoiceAssistant',
                            'DownloadModel',
                            new GLib.Variant('(ss)', [m.provider, m.id]),
                            new GLib.VariantType('(b)'),
                            Gio.DBusCallFlags.NONE,
                            -1,
                            null,
                            (source, res) => {
                                try {
                                    source.call_finish(res);
                                    window.add_toast(new Adw.Toast({
                                        title: _(`Richiesta di download inviata al demone per ${m.name}`)
                                    }));
                                } catch (e) {
                                    fallbackLocalDownload();
                                }
                            }
                        );
                    } catch (e) {
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

                checkBtn.connect('toggled', () => {
                    if (checkBtn.active && isInstalled) {
                        selectModel();
                    }
                });

                if (isDownloading) {
                    const progressBar = new Gtk.ProgressBar({
                        valign: Gtk.Align.CENTER,
                        width_request: 140,
                        show_text: true,
                        fraction: Math.min(1.0, Math.max(0.0, downloadPct / 100.0)),
                        text: `${downloadPct}%`
                    });

                    downloadButtons.set(modelKey, { progressBar });

                    const box = new Gtk.Box({ spacing: 6, valign: Gtk.Align.CENTER });
                    box.append(progressBar);
                    row.add_suffix(box);
                } else if (isInstalled && isCurrent) {
                    const activeIcon = Gtk.Image.new_from_icon_name('check-plain-symbolic');
                    activeIcon.valign = Gtk.Align.CENTER;
                    activeIcon.add_css_class('accent');
                    activeIcon.tooltip_text = _('Modello attualmente in uso');
                    row.add_suffix(activeIcon);
                } else if (isInstalled && !isCurrent) {
                    const deleteBtn = Gtk.Button.new_from_icon_name('user-trash-symbolic');
                    deleteBtn.valign = Gtk.Align.CENTER;
                    deleteBtn.add_css_class('flat');
                    deleteBtn.add_css_class('error');
                    deleteBtn.tooltip_text = _('Elimina modello dal disco');
                    deleteBtn.connect('clicked', () => {
                        let modelsPath = getModelsPath();
                        let targetDir = (m.provider === 'whisper') ? `${modelsPath}/whisper-${m.id}` : `${modelsPath}/${m.id}`;
                        let folderFile = Gio.File.new_for_path(targetDir);
                        try {
                            folderFile.delete_async(GLib.PRIORITY_DEFAULT, null, (f, res) => {
                                try {
                                    f.delete_finish(res);
                                } catch (err) {
                                    GLib.spawn_command_line_sync(`rm -rf "${targetDir}"`);
                                }
                                GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
                                    renderModelList();
                                    if (typeof refreshCacheGroup === 'function') refreshCacheGroup();
                                    return GLib.SOURCE_REMOVE;
                                });
                            });
                        } catch (e) {
                            GLib.spawn_command_line_sync(`rm -rf "${targetDir}"`);
                            GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
                                renderModelList();
                                if (typeof refreshCacheGroup === 'function') refreshCacheGroup();
                                return GLib.SOURCE_REMOVE;
                            });
                        }
                    });

                    row.add_suffix(deleteBtn);
                } else if (!isInstalled) {
                    const currentDownloadBtn = Gtk.Button.new_from_icon_name('folder-download-symbolic');
                    currentDownloadBtn.valign = Gtk.Align.CENTER;
                    currentDownloadBtn.add_css_class('flat');
                    currentDownloadBtn.tooltip_text = _('Scarica modello');

                    currentDownloadBtn.connect('clicked', () => startDownload());

                    const box = new Gtk.Box({ spacing: 6, valign: Gtk.Align.CENTER });
                    box.append(currentDownloadBtn);
                    row.add_suffix(box);
                }

                modelsGroupContainer.add(row);
                activeModelGroupRows.push(row);
            });
        };

        searchEntry.connect('search-changed', () => renderModelList());

        // Recupero modelli Vosk online / DBus
        const fetchVoskModelsHttp = () => {
            try {
                let cmd = ['curl', '-s', 'https://alphacephei.com/vosk/models/model-list.json'];
                let proc = new Gio.Subprocess({ argv: cmd, flags: Gio.SubprocessFlags.STDOUT_PIPE });
                proc.init(null);
                proc.communicate_utf8_async(null, null, (p, res) => {
                    try {
                        let [, stdout] = p.communicate_utf8_finish(res);
                        if (stdout) {
                            let raw = JSON.parse(stdout);
                            if (Array.isArray(raw)) {
                                let models = [];
                                raw.forEach(item => {
                                    if (item.obsolete !== 'true') {
                                        let m_id = item.name;
                                        let m_lang = item.lang_text || item.lang || 'en';
                                        let m_size = item.size_text || '';
                                        let m_url = item.url || `https://alphacephei.com/vosk/models/${m_id}.zip`;
                                        models.push({
                                            id: m_id,
                                            provider: 'vosk',
                                            name: `${m_lang} - ${m_id}`,
                                            subtitle: m_size ? `Vosk • ${m_size} • ${m_lang}` : `Vosk • ${m_lang}`,
                                            url: m_url,
                                            lang: item.lang || 'en',
                                            lang_text: m_lang,
                                            size_text: m_size
                                        });
                                    }
                                });
                                models.sort((a, b) => {
                                    let lA = (a.lang || '').toLowerCase();
                                    let lB = (b.lang || '').toLowerCase();
                                    if (lA === 'it' && lB !== 'it') return -1;
                                    if (lA !== 'it' && lB === 'it') return 1;
                                    if (lA === 'en' && lB !== 'en') return -1;
                                    if (lA !== 'en' && lB === 'en') return 1;
                                    return a.name.localeCompare(b.name);
                                });
                                if (models.length > 0) {
                                    fetchedVoskModels = models;
                                    renderModelList();
                                }
                            }
                        }
                    } catch (err) {}
                });
            } catch (err) {}
        };

        const fetchVoskModels = () => {
            try {
                Gio.DBus.session.call(
                    'org.local.VoiceAssistant',
                    '/org/local/VoiceAssistant',
                    'org.local.VoiceAssistant',
                    'GetAvailableModels',
                    new GLib.Variant('(s)', ['vosk']),
                    new GLib.VariantType('(s)'),
                    Gio.DBusCallFlags.NONE,
                    5000,
                    null,
                    (source, res) => {
                        try {
                            const val = source.call_finish(res);
                            const jsonStr = val.unpack()[0];
                            const rawModels = JSON.parse(jsonStr);
                            if (Array.isArray(rawModels) && rawModels.length > 0) {
                                fetchedVoskModels = rawModels.map(m => ({
                                    id: m.id,
                                    provider: 'vosk',
                                    name: m.name,
                                    subtitle: m.size_text ? `Vosk • ${m.size_text} • ${m.lang_text}` : `Vosk • ${m.lang_text}`,
                                    url: m.url,
                                    lang: m.lang,
                                    lang_text: m.lang_text,
                                    size_text: m.size_text
                                }));
                                renderModelList();
                                return;
                            }
                        } catch (e) {
                            fetchVoskModelsHttp();
                        }
                    }
                );
            } catch (e) {
                fetchVoskModelsHttp();
            }
        };

        fetchVoskModels();
        renderModelList();


        // ==========================================
        // 3. PAGINA INTELLIGENZA ARTIFICIALE (LLM)
        // ==========================================
        const llmPage = new Adw.PreferencesPage();

        const llmGroup = new Adw.PreferencesGroup({
            title: _('Provider AI / LLM'),
            description: _('Seleziona l\'elaboratore per la comprensione delle intenzioni e la generazione di risposte.')
        });
        llmPage.add(llmGroup);

        const llmOptions = [
            { id: 'ollama', title: _('Ollama (Locale, raccomandato)'), subtitle: _('Esecuzione locale tramite Ollama server') },
            { id: 'llama.cpp', title: _('Llama.cpp / LocalAI'), subtitle: _('Server locale compatibile OpenAI') },
            { id: 'openai', title: _('OpenAI API (Cloud)'), subtitle: _('API ufficiali cloud OpenAI') },
            { id: 'disabled', title: _('Disattivato'), subtitle: _('Solo esecuzione comandi diretti senza LLM') }
        ];

        let currentLlmProvider = settings.get_string('llm-provider') || 'ollama';
        let llmFirstRadio = null;

        llmOptions.forEach((opt) => {
            const row = new Adw.ActionRow({
                title: opt.title,
                subtitle: opt.subtitle,
                activatable: true
            });
            const checkBtn = new Gtk.CheckButton({
                valign: Gtk.Align.CENTER,
                margin_end: 12
            });
            if (!llmFirstRadio) {
                llmFirstRadio = checkBtn;
            } else {
                checkBtn.set_group(llmFirstRadio);
            }
            if (opt.id === currentLlmProvider) {
                checkBtn.active = true;
            }

            row.add_prefix(checkBtn);

            const selectLlm = () => {
                checkBtn.active = true;
                settings.set_string('llm-provider', opt.id);
            };

            row.connect('activated', selectLlm);
            checkBtn.connect('toggled', () => {
                if (checkBtn.active) settings.set_string('llm-provider', opt.id);
            });

            llmGroup.add(row);
        });

        const llmConfigGroup = new Adw.PreferencesGroup({
            title: _('Configurazione Modello e Server')
        });
        llmPage.add(llmConfigGroup);

        const llmModelRow = new Adw.EntryRow({
            title: _('Modello Selezionato'),
            text: settings.get_string('llm-model') || 'llama3.2:3b'
        });
        llmModelRow.connect('changed', () => {
            settings.set_string('llm-model', llmModelRow.text);
        });
        llmConfigGroup.add(llmModelRow);

        const llmEndpointRow = new Adw.EntryRow({
            title: _('URL Server / Endpoint API'),
            text: settings.get_string('llm-endpoint') || 'http://localhost:11434'
        });
        llmEndpointRow.connect('changed', () => {
            settings.set_string('llm-endpoint', llmEndpointRow.text);
        });

        // Pulsante di test connessione
        const testConnBtn = new Gtk.Button({
            label: _('Test Connessione'),
            valign: Gtk.Align.CENTER
        });
        const testConnSpinner = new Gtk.Spinner({ visible: false, margin_end: 6 });
        testConnBtn.connect('clicked', () => {
            testConnBtn.sensitive = false;
            testConnSpinner.visible = true;
            testConnSpinner.start();

            let endpoint = llmEndpointRow.text.trim();
            let cmd = ['curl', '-s', '-o', '/dev/null', '-w', '%{http_code}', `${endpoint}/api/tags`];
            try {
                let proc = new Gio.Subprocess({ argv: cmd, flags: Gio.SubprocessFlags.STDOUT_PIPE });
                proc.init(null);
                proc.communicate_utf8_async(null, null, (p, res) => {
                    testConnSpinner.stop();
                    testConnSpinner.visible = false;
                    testConnBtn.sensitive = true;
                    try {
                        let [, stdout] = p.communicate_utf8_finish(res);
                        if (stdout && stdout.trim() === '200') {
                            testConnBtn.label = _('Connesso!');
                        } else {
                            testConnBtn.label = _('Errore Server');
                        }
                    } catch (e) {
                        testConnBtn.label = _('Non Raggiungibile');
                    }
                    GLib.timeout_add(GLib.PRIORITY_DEFAULT, 3000, () => {
                        testConnBtn.label = _('Test Connessione');
                        return GLib.SOURCE_REMOVE;
                    });
                });
            } catch (e) {
                testConnSpinner.stop();
                testConnSpinner.visible = false;
                testConnBtn.sensitive = true;
                testConnBtn.label = _('Errore');
            }
        });

        const testBox = new Gtk.Box({ spacing: 6 });
        testBox.append(testConnSpinner);
        testBox.append(testConnBtn);
        llmEndpointRow.add_suffix(testBox);
        llmConfigGroup.add(llmEndpointRow);

        // Gruppo Personalità e System Prompt
        const llmPromptGroup = new Adw.PreferencesGroup({
            title: _('Personalità e Istruzioni'),
            description: _('Definisci le istruzioni di sistema per la risposta dell\'assistente.')
        });
        llmPage.add(llmPromptGroup);

        const llmPromptRow = new Adw.EntryRow({
            title: _('Prompt di Sistema'),
            text: settings.get_string('llm-system-prompt') || 'Sei un assistente vocale per GNOME Shell. Rispondi in modo breve e amichevole.'
        });
        llmPromptRow.connect('changed', () => {
            settings.set_string('llm-system-prompt', llmPromptRow.text);
        });
        llmPromptGroup.add(llmPromptRow);


        // ==========================================
        // 4. PAGINA SINTESI VOCALE (TTS)
        // ==========================================
        const ttsPage = new Adw.PreferencesPage();

        const ttsMainGroup = new Adw.PreferencesGroup({
            title: _('Motore di Sintesi Vocale (TTS)'),
            description: _('Configura la voce offline per la riproduzione parlata delle risposte.')
        });
        ttsPage.add(ttsMainGroup);

        const ttsEnableRow = new Adw.SwitchRow({
            title: _('Abilita Sintesi Vocale'),
            subtitle: _('Riproduci a voce le risposte generate dall\'assistente'),
            active: settings.get_boolean('tts-enabled')
        });
        ttsEnableRow.connect('notify::active', () => {
            settings.set_boolean('tts-enabled', ttsEnableRow.active);
        });
        ttsMainGroup.add(ttsEnableRow);

        const ttsProviderGroup = new Adw.PreferencesGroup({
            title: _('Provider Sintesi Vocale'),
            description: _('Seleziona il motore di sintesi vocale da utilizzare.')
        });
        ttsPage.add(ttsProviderGroup);

        const ttsOptions = [
            { id: 'piper', title: _('Piper TTS (Offline, Raccomandato)'), subtitle: _('Sintesi vocale locale ad alta velocità e qualità naturale') },
            { id: 'coqui', title: _('Coqui / XTTS (Offline, Alta Qualità)'), subtitle: _('Modello di sintesi avanzato') },
            { id: 'espeak', title: _('eSpeak NG'), subtitle: _('Sintesi ultra-leggera e sintetica') },
            { id: 'disabled', title: _('Disattivato'), subtitle: _('Nessuna riproduzione audio') }
        ];

        let currentTtsProvider = settings.get_string('tts-provider') || 'piper';
        let ttsFirstRadio = null;

        ttsOptions.forEach((opt) => {
            const row = new Adw.ActionRow({
                title: opt.title,
                subtitle: opt.subtitle,
                activatable: true
            });
            const checkBtn = new Gtk.CheckButton({
                valign: Gtk.Align.CENTER,
                margin_end: 12
            });
            if (!ttsFirstRadio) {
                ttsFirstRadio = checkBtn;
            } else {
                checkBtn.set_group(ttsFirstRadio);
            }
            if (opt.id === currentTtsProvider) {
                checkBtn.active = true;
            }

            row.add_prefix(checkBtn);

            const selectTts = () => {
                checkBtn.active = true;
                settings.set_string('tts-provider', opt.id);
            };

            row.connect('activated', selectTts);
            checkBtn.connect('toggled', () => {
                if (checkBtn.active) settings.set_string('tts-provider', opt.id);
            });

            ttsProviderGroup.add(row);
        });

        const ttsVoiceRow = new Adw.ActionRow({
            title: _('Voce Selezionata'),
            subtitle: settings.get_string('tts-voice') || 'it_IT-paola-medium'
        });

        const ttsTestBtn = new Gtk.Button({
            label: _('Prova Voce'),
            valign: Gtk.Align.CENTER
        });
        ttsTestBtn.connect('clicked', () => {
            ttsTestBtn.label = _('Riproduzione...');
            ttsTestBtn.sensitive = false;
            let testText = 'Ciao! Questa è una prova della sintesi vocale dell assistente.';
            let cmd = ['sh', '-c', `spd-say "${testText}" || espeak -v it "${testText}"`];
            try {
                let proc = new Gio.Subprocess({ argv: cmd, flags: Gio.SubprocessFlags.NONE });
                proc.init(null);
                proc.wait_check_async(null, (p, res) => {
                    ttsTestBtn.label = _('Prova Voce');
                    ttsTestBtn.sensitive = true;
                });
            } catch (e) {
                ttsTestBtn.label = _('Prova Voce');
                ttsTestBtn.sensitive = true;
            }
        });
        ttsVoiceRow.add_suffix(ttsTestBtn);
        ttsMainGroup.add(ttsVoiceRow);

        // Gruppo Parametri Audio
        const ttsParamsGroup = new Adw.PreferencesGroup({
            title: _('Parametri Riproduzione Audio')
        });
        ttsPage.add(ttsParamsGroup);

        const ttsSpeedRow = new Adw.ActionRow({
            title: _('Velocità Parlato'),
            subtitle: _('Regola la velocità di lettura delle risposte (0.5x - 2.0x)')
        });

        const speedAdjustment = new Gtk.Adjustment({
            value: settings.get_double('tts-speed') || 1.0,
            lower: 0.5,
            upper: 2.0,
            step_increment: 0.1
        });
        const speedSpinBtn = new Gtk.SpinButton({
            adjustment: speedAdjustment,
            digits: 1,
            valign: Gtk.Align.CENTER
        });
        speedSpinBtn.connect('value-changed', () => {
            settings.set_double('tts-speed', speedSpinBtn.value);
        });
        ttsSpeedRow.add_suffix(speedSpinBtn);
        ttsParamsGroup.add(ttsSpeedRow);


        // ==========================================
        // 5. PAGINA MODEL CONTEXT PROTOCOL (MCP)
        // ==========================================
        const mcpPage = new Adw.PreferencesPage();

        const mcpMainGroup = new Adw.PreferencesGroup({
            title: _('Model Context Protocol (MCP)'),
            description: _('Consente al modello LLM di accedere a strumenti locali, azioni di sistema e contesti esterni.')
        });
        mcpPage.add(mcpMainGroup);

        const mcpEnableRow = new Adw.SwitchRow({
            title: _('Abilita Integrazione MCP'),
            subtitle: _('Permetti all\'assistente di utilizzare strumenti e server MCP per eseguire azioni'),
            active: settings.get_boolean('mcp-enabled')
        });
        mcpEnableRow.connect('notify::active', () => {
            settings.set_boolean('mcp-enabled', mcpEnableRow.active);
        });
        mcpMainGroup.add(mcpEnableRow);

        const mcpServersGroup = new Adw.PreferencesGroup({
            title: _('Server e Strumenti MCP Configurati'),
            description: _('Strumenti e contesti abilitati per l\'assistente vocale.')
        });
        mcpPage.add(mcpServersGroup);

        // Server integrato per il controllo di sistema GNOME
        const gnomeSysRow = new Adw.ActionRow({
            title: _('Controllo Sistema GNOME'),
            subtitle: _('Strumento interno (Volume, Luminosità, Controllo Finestre, App)')
        });
        const gnomeSysSwitch = new Gtk.Switch({
            active: true,
            valign: Gtk.Align.CENTER
        });
        gnomeSysRow.add_suffix(gnomeSysSwitch);
        mcpServersGroup.add(gnomeSysRow);

        // Sezione Aggiungi Nuovo Server MCP
        const mcpAddGroup = new Adw.PreferencesGroup({
            title: _('Aggiungi Nuovo Server MCP'),
            description: _('Configura un server MCP esterno via STDIO o HTTP SSE.')
        });
        mcpPage.add(mcpAddGroup);

        const mcpNameRow = new Adw.EntryRow({
            title: _('Nome Server / Strumento')
        });
        mcpAddGroup.add(mcpNameRow);

        const mcpCommandRow = new Adw.EntryRow({
            title: _('Comando o Endpoint (es. npx -y @mcp/filesystem)')
        });
        const addMcpBtn = new Gtk.Button({
            label: _('Aggiungi'),
            valign: Gtk.Align.CENTER
        });
        addMcpBtn.add_css_class('suggested-action');
        addMcpBtn.connect('clicked', () => {
            let name = mcpNameRow.text.trim();
            let cmd = mcpCommandRow.text.trim();
            if (!name || !cmd) return;

            const newRow = new Adw.ActionRow({
                title: name,
                subtitle: cmd
            });
            const delBtn = Gtk.Button.new_from_icon_name('user-trash-symbolic');
            delBtn.valign = Gtk.Align.CENTER;
            delBtn.add_css_class('flat');
            delBtn.connect('clicked', () => {
                mcpServersGroup.remove(newRow);
            });
            newRow.add_suffix(delBtn);
            mcpServersGroup.add(newRow);

            mcpNameRow.text = '';
            mcpCommandRow.text = '';
        });
        mcpCommandRow.add_suffix(addMcpBtn);
        mcpAddGroup.add(mcpCommandRow);


        // ==========================================
        // 6. PAGINA ARCHIVIAZIONE E MODELLI
        // ==========================================
        const modelsPage = new Adw.PreferencesPage();
        const modelsFolderGroup = new Adw.PreferencesGroup({
            title: _('Cartella Modelli'),
            description: _('Seleziona la cartella in cui vengono salvati e cercati i modelli vocali.')
        });

        const modelsFolderRow = new Adw.ActionRow({
            title: _('Cartella di Salvataggio Modelli'),
            subtitle: formatPathForDisplay(getModelsPath())
        });

        const selectFolderBtn = Gtk.Button.new_from_icon_name('folder-open-symbolic');
        selectFolderBtn.valign = Gtk.Align.CENTER;
        selectFolderBtn.add_css_class('flat');
        selectFolderBtn.tooltip_text = _('Seleziona cartella modelli');

        selectFolderBtn.connect('clicked', () => {
            const chooser = new Gtk.FileChooserNative({
                title: _('Seleziona Cartella Modelli'),
                action: Gtk.FileChooserAction.SELECT_FOLDER,
                modal: true,
                transient_for: window
            });

            chooser.connect('response', (dialog, response_id) => {
                if (response_id === Gtk.ResponseType.ACCEPT) {
                    const file = dialog.get_file();
                    if (file) {
                        const newPath = file.get_path();
                        settings.set_string('models-dir', newPath);
                        modelsFolderRow.subtitle = formatPathForDisplay(newPath);
                        refreshCacheGroup();
                    }
                }
                chooser.destroy();
            });

            chooser.show();
        });

        const openFolderBtn = Gtk.Button.new_from_icon_name('web-browser-symbolic');
        openFolderBtn.valign = Gtk.Align.CENTER;
        openFolderBtn.add_css_class('flat');
        openFolderBtn.tooltip_text = _('Apri cartella nel File Manager');

        openFolderBtn.connect('clicked', () => {
            const currentPath = getModelsPath();
            GLib.mkdir_with_parents(currentPath, 0o755);
            Gio.AppInfo.launch_default_for_uri('file://' + currentPath, null);
        });

        const resetFolderBtn = Gtk.Button.new_from_icon_name('edit-clear-symbolic');
        resetFolderBtn.valign = Gtk.Align.CENTER;
        resetFolderBtn.add_css_class('flat');
        resetFolderBtn.tooltip_text = _('Ripristina cartella predefinita');
        resetFolderBtn.connect('clicked', () => {
            settings.reset('models-dir');
            const defaultPath = getModelsPath();
            modelsFolderRow.subtitle = formatPathForDisplay(defaultPath);
            refreshCacheGroup();
        });

        const folderButtonsBox = new Gtk.Box({ spacing: 4 });
        folderButtonsBox.append(selectFolderBtn);
        folderButtonsBox.append(openFolderBtn);
        folderButtonsBox.append(resetFolderBtn);
        modelsFolderRow.add_suffix(folderButtonsBox);
        modelsFolderGroup.add(modelsFolderRow);

        const cacheGroup = new Adw.PreferencesGroup({
            title: _('Modelli Scaricati'),
            description: _('Gestisci i modelli memorizzati nel sistema')
        });

        const cleanUnusedBtn = new Gtk.Button({
            label: _('Elimina Inutilizzati'),
            valign: Gtk.Align.CENTER,
            css_classes: ['destructive-action']
        });

        cleanUnusedBtn.connect('clicked', () => {
            const currentModelsPath = getModelsPath();
            let activeModel = settings.get_string('stt-model') || '';
            let activeProvider = settings.get_string('stt-provider') || 'vosk';
            let activeFolderName = activeProvider === 'whisper' ? `whisper-${activeModel}` : activeModel;

            const modelsDir = Gio.File.new_for_path(currentModelsPath);
            let countRemoved = 0;
            try {
                let enumerator = modelsDir.enumerate_children('standard::name,standard::type', Gio.FileQueryInfoFlags.NONE, null);
                let info;
                while ((info = enumerator.next_file(null)) !== null) {
                    if (info.get_file_type() === Gio.FileType.DIRECTORY) {
                        let folderName = info.get_name();
                        if (!folderName.startsWith('.') && folderName !== activeFolderName) {
                            let targetPath = `${currentModelsPath}/${folderName}`;
                            GLib.spawn_command_line_sync(`rm -rf "${targetPath}"`);
                            countRemoved++;
                        }
                    }
                }
            } catch (e) {}

            refreshCacheGroup();
            renderModelList();
            window.add_toast(new Adw.Toast({
                title: countRemoved > 0 
                    ? _('Modelli inutilizzati eliminati con successo!') 
                    : _('Nessun modello inutilizzato da eliminare.')
            }));
        });

        cacheGroup.set_header_suffix(cleanUnusedBtn);

        let activeCacheRows = [];
        refreshCacheGroup = () => {
            for (const r of activeCacheRows) {
                try {
                    cacheGroup.remove(r);
                } catch (e) {}
            }
            activeCacheRows = [];

            const currentModelsPath = getModelsPath();
            cacheGroup.set_description(_(`Gestisci i modelli scaricati in ${formatPathForDisplay(currentModelsPath)}`));

            let activeModel = settings.get_string('stt-model') || '';
            let activeProvider = settings.get_string('stt-provider') || 'vosk';
            let activeFolderName = activeProvider === 'whisper' ? `whisper-${activeModel}` : activeModel;

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
            } catch (e) {}

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
        modelsPage.add(cacheGroup);

        // ==========================================
        // 4. PAGINA INFORMAZIONI
        // ==========================================
        const aboutPage = new Adw.PreferencesPage();
        const aboutGroup = new Adw.PreferencesGroup({
            title: _('Voice Assistant')
        });
        aboutPage.add(aboutGroup);

        const nameRow = new Adw.ActionRow({
            title: _('Voice Assistant GNOME Extension'),
            subtitle: _('Assistente vocale offline e integrato per GNOME Shell')
        });
        aboutGroup.add(nameRow);

        const versionRow = new Adw.ActionRow({
            title: _('Versione'),
            subtitle: '1.0.0'
        });
        aboutGroup.add(versionRow);

        const dbusRow = new Adw.ActionRow({
            title: _('Servizio D-Bus Backend'),
            subtitle: 'org.local.VoiceAssistant'
        });
        aboutGroup.add(dbusRow);

        const docRow = new Adw.ActionRow({
            title: _('Codice Sorgente e Documentazione'),
            subtitle: 'https://github.com/mkswap/voice-assistant'
        });
        const docBtn = Gtk.Button.new_from_icon_name('web-browser-symbolic');
        docBtn.valign = Gtk.Align.CENTER;
        docBtn.add_css_class('flat');
        docBtn.connect('clicked', () => {
            Gio.AppInfo.launch_default_for_uri('https://github.com/mkswap/voice-assistant', null);
        });
        docRow.add_suffix(docBtn);
        aboutGroup.add(docRow);



        // ==========================================
        // COSTRIZIONE ADW.NAVIGATION_SPLIT_VIEW
        // ==========================================
        const pages = [
            { id: 'general', title: _('Generali'), icon: 'preferences-system-symbolic', widget: generalPage },
            { id: 'stt', title: _('Motore Vocale (STT)'), icon: 'audio-input-microphone-symbolic', widget: sttPage },
            { id: 'llm', title: _('Intelligenza Artificiale (LLM)'), icon: 'brain-augemnted-symbolic', widget: llmPage },
            { id: 'tts', title: _('Sintesi Vocale (TTS)'), icon: 'audio-volume-high-symbolic', widget: ttsPage },
            { id: 'mcp', title: _('Strumenti (MCP)'), icon: 'system-run-symbolic', widget: mcpPage },
            { id: 'models', title: _('Archiviazione e Modelli'), icon: 'drive-harddisk-symbolic', widget: modelsPage },
            { id: 'about', title: _('Informazioni'), icon: 'help-about-symbolic', widget: aboutPage }
        ];

        const splitView = new Adw.NavigationSplitView({
            min_sidebar_width: 260
        });

        // Breakpoint per supporto mobile (< 600px)
        const mobileBreakpoint = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse('max-width: 600px')
        );
        mobileBreakpoint.add_setter(splitView, 'collapsed', true);
        window.add_breakpoint(mobileBreakpoint);

        // 1. SIDEBAR PAGE (Adw.NavigationPage)
        const sidebarToolbarView = new Adw.ToolbarView();
        const sidebarHeaderBar = new Adw.HeaderBar({
            title_widget: new Adw.WindowTitle({
                title: _('Preferenze')
            })
        });
        sidebarToolbarView.add_top_bar(sidebarHeaderBar);

        const sidebarListBox = new Gtk.ListBox({
            css_classes: ['navigation-sidebar'],
            selection_mode: Gtk.SelectionMode.SINGLE
        });

        const pageRows = [];
        pages.forEach((p) => {
            const row = new Adw.ActionRow({
                title: p.title,
                activatable: true
            });
            const icon = new Gtk.Image({
                icon_name: p.icon,
                margin_end: 12
            });
            row.add_prefix(icon);
            sidebarListBox.append(row);
            pageRows.push(row);
        });

        const sidebarScroll = new Gtk.ScrolledWindow({
            hscrollbar_policy: Gtk.PolicyType.NEVER,
            vexpand: true
        });
        sidebarScroll.set_child(sidebarListBox);
        sidebarToolbarView.set_content(sidebarScroll);

        const sidebarPage = new Adw.NavigationPage({
            child: sidebarToolbarView,
            title: _('Preferenze')
        });
        sidebarPage.add_css_class('sidebar');

        splitView.set_sidebar(sidebarPage);

        // 2. CONTENT NAVIGATION CONTAINER (Adw.NavigationView)
        const contentNavigationView = new Adw.NavigationView();

        const contentToolbarView = new Adw.ToolbarView();
        const contentHeaderBar = new Adw.HeaderBar();
        const contentTitle = new Adw.WindowTitle({
            title: pages[0].title
        });
        contentHeaderBar.set_title_widget(contentTitle);
        contentToolbarView.add_top_bar(contentHeaderBar);

        const stack = new Gtk.Stack({
            transition_type: Gtk.StackTransitionType.CROSSFADE,
            vexpand: true,
            hexpand: true
        });

        pages.forEach(p => {
            stack.add_named(p.widget, p.id);
        });

        const contentScroll = new Gtk.ScrolledWindow({
            hscrollbar_policy: Gtk.PolicyType.NEVER,
            vexpand: true
        });
        contentScroll.set_child(stack);
        contentToolbarView.set_content(contentScroll);

        // Toolbar Footer con pulsante "Applica Modifiche"
        const createFooterActionBar = () => {
            const footerActionBar = new Gtk.ActionBar();
            const applyBtn = new Gtk.Button({
                label: _('Applica Modifiche'),
                css_classes: ['suggested-action', 'pill'],
                valign: Gtk.Align.CENTER,
                halign: Gtk.Align.CENTER
            });

            applyBtn.sensitive = (pendingProvider !== activeProvider || pendingModel !== activeModel);
            applyButtons.push(applyBtn);

            applyBtn.connect('clicked', () => {
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
                    } catch (e) {}

                    updateApplyButtons();
                    renderModelList();
                    if (typeof refreshCacheGroup === 'function') refreshCacheGroup();

                    window.add_toast(new Adw.Toast({
                        title: _('Modifiche applicate con successo!')
                    }));
                }
            });

            footerActionBar.set_center_widget(applyBtn);
            return footerActionBar;
        };

        contentToolbarView.add_bottom_bar(createFooterActionBar());
        selectorToolbarView.add_bottom_bar(createFooterActionBar());

        const mainContentNavPage = new Adw.NavigationPage({
            child: contentToolbarView,
            title: pages[0].title
        });

        contentNavigationView.add(mainContentNavPage);

        const contentWrapperPage = new Adw.NavigationPage({
            child: contentNavigationView,
            title: _('Contenuto')
        });

        splitView.set_content(contentWrapperPage);

        // Handler per l'apertura della sotto-pagina del Selettore Modelli
        const openSelector = () => {
            renderModelList();
            contentNavigationView.push(modelSelectorPage);
            splitView.set_show_content(true);
        };

        currentModelRow.connect('activated', openSelector);
        openModelSelectorBtn.connect('clicked', openSelector);

        // Selezione riga sidebar -> Mostra pagina nel contenuto (e torna al livello principale se in sotto-pagina)
        sidebarListBox.connect('row-selected', (listbox, row) => {
            if (!row) return;
            const index = pageRows.indexOf(row);
            if (index >= 0 && index < pages.length) {
                const selectedPage = pages[index];
                
                // Se eravamo dentro la sottopagina Selettore Modelli, torniamo alla pagina principale
                if (contentNavigationView.get_visible_page() === modelSelectorPage) {
                    contentNavigationView.pop();
                }

                stack.set_visible_child_name(selectedPage.id);
                contentTitle.set_title(selectedPage.title);
                mainContentNavPage.set_title(selectedPage.title);

                // In modalità mobile/collassata, passa alla vista di dettaglio
                splitView.set_show_content(true);
            }
        });

        // Seleziona la prima riga di default
        sidebarListBox.select_row(pageRows[0]);

        // Imposta Adw.NavigationSplitView come contenuto root della finestra
        window.set_content(splitView);
    }
}
