import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Gtk from 'gi://Gtk';
import Adw from 'gi://Adw';
import Soup from 'gi://Soup';
import { getModelsPath } from './utils.js';

export function setupModelSelector(builder, settings, window, path, _, getRefreshCacheGroup) {
    let currentSelectorMode = 'stt';
    let fetchedVoskModels = [];
    let fetchedLlmModels = [];

    const downloadingProgress = new Map();
    const downloadButtons = new Map();

    const localLlmModels = [];

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

    const searchEntry = builder.get_object('search_entry');
    const selectorViewStack = builder.get_object('selector_view_stack');
    const selectorViewSwitcher = builder.get_object('selector_view_switcher');
    const selectorViewSwitcherBar = builder.get_object('selector_view_switcher_bar');
    const modelSelectorPage = builder.get_object('model_selector_page');

    const modelsGroupAll = builder.get_object('models_group_all');
    const modelsGroupInstalled = builder.get_object('models_group_installed');
    const modelsGroupDownloading = builder.get_object('models_group_downloading');

    let renderModelList = () => { };

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
                            const refresh = getRefreshCacheGroup();
                            if (typeof refresh === 'function') refresh();
                            return GLib.SOURCE_REMOVE;
                        });
                    }
                } catch (e) { }
            }
        );
    } catch (e) { }

    const processLlmModelsJson = (json) => {
        if (Array.isArray(json) && json.length > 0) {
            let seen = new Set();
            let list = [];

            for (let item of json) {
                let id = item.id || item.modelId;
                let repo = item.repo || id;
                let file = item.file || (repo ? `${repo.split('/').pop().replace(/-GGUF/i, '').replace(/-gguf/i, '')}-Q4_K_M.gguf` : id);
                let modelId = (repo && file && !id.includes(':')) ? `${repo}:${file}` : id;

                if (!seen.has(modelId) && !seen.has(file)) {
                    seen.add(modelId);
                    let downloads = item.downloads || 0;
                    let likes = item.likes || 0;
                    let subtitle = item.subtitle || `Hugging Face • ${downloads.toLocaleString()} downloads • ${likes} likes`;
                    list.push({
                        id: modelId,
                        provider: 'llm',
                        name: item.name || repo || id,
                        subtitle: subtitle,
                        repo: repo,
                        file: file,
                        lang: 'multilingual',
                        lang_text: 'Multilingual',
                        size_text: item.size_text || 'GGUF',
                        url: item.url || `https://huggingface.co/${repo}/resolve/main/${file}`
                    });
                }
            }

            if (list.length > 0) {
                fetchedLlmModels = list;
                renderModelList();
            }
        }
    };

    const fetchLlmModelsSoup = (query, callback) => {
        try {
            let url = 'https://huggingface.co/api/models?filter=gguf&sort=downloads&direction=-1&limit=100';
            if (query && query.trim()) {
                url += `&search=${encodeURIComponent(query.trim())}`;
            }
            let message = Soup.Message.new('GET', url);
            let session = new Soup.Session();
            session.send_and_read_async(message, GLib.PRIORITY_DEFAULT, null, (sess, res) => {
                try {
                    let bytes = session.send_and_read_finish(res);
                    if (bytes) {
                        let text = new TextDecoder().decode(bytes.get_data());
                        let json = JSON.parse(text);
                        callback(json);
                    }
                } catch (err) { }
            });
        } catch (err) { }
    };

    const fetchLlmModelsOnline = (query = '') => {
        try {
            let bus = Gio.DBus.session;
            let arg = query ? `llm:${query}` : 'llm';
            bus.call(
                'org.local.VoiceAssistant',
                '/org/local/VoiceAssistant',
                'org.local.VoiceAssistant',
                'GetAvailableModels',
                new GLib.Variant('(s)', [arg]),
                null,
                Gio.DBusCallFlags.NONE,
                -1,
                null,
                (conn, res) => {
                    try {
                        let reply = conn.call_finish(res);
                        let [jsonStr] = reply.recursiveUnpack();
                        let json = JSON.parse(jsonStr);
                        if (Array.isArray(json) && json.length > 0) {
                            processLlmModelsJson(json);
                        } else {
                            fetchLlmModelsSoup(query, processLlmModelsJson);
                        }
                    } catch (e) {
                        fetchLlmModelsSoup(query, processLlmModelsJson);
                    }
                }
            );
        } catch (err) {
            fetchLlmModelsSoup(query, processLlmModelsJson);
        }
    };

    fetchLlmModelsOnline();

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
                    } catch (err) { }
                });
            } catch (err) { }
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

    const updateResponsiveSwitcher = () => {
        let width = window.default_width || window.get_allocated_width();
        let isNarrow = (width < 650);
        if (selectorViewSwitcherBar) selectorViewSwitcherBar.reveal = isNarrow;
        if (selectorViewSwitcher) selectorViewSwitcher.visible = !isNarrow;
    };

    window.connect('notify::default-width', updateResponsiveSwitcher);
    updateResponsiveSwitcher();

    if (selectorViewStack) selectorViewStack.connect('notify::visible-child-name', () => renderModelList());
    if (searchEntry) {
        searchEntry.connect('search-changed', () => {
            if (currentSelectorMode === 'llm' && searchEntry.text.trim().length >= 3) {
                fetchLlmModelsOnline(searchEntry.text.trim());
            }
            renderModelList();
        });
    }

    let activeModelGroupRows = [];

    renderModelList = () => {
        for (const item of activeModelGroupRows) {
            try {
                item.group.remove(item.row);
            } catch (e) { }
        }
        activeModelGroupRows = [];

        const isLlm = (currentSelectorMode === 'llm');
        const basePath = getModelsPath(settings);
        const installedSet = new Set();

        const scanDir = (dirPath) => {
            try {
                let dir = Gio.File.new_for_path(dirPath);
                let enumerator = dir.enumerate_children('standard::name,standard::type', Gio.FileQueryInfoFlags.NONE, null);
                let info;
                while ((info = enumerator.next_file(null)) !== null) {
                    installedSet.add(info.get_name());
                }
            } catch (e) { }
        };

        if (isLlm) {
            scanDir(basePath + '/llm');
        } else {
            scanDir(basePath + '/stt');
            scanDir(basePath);
        }

        const allModels = isLlm ? (fetchedLlmModels.length > 0 ? fetchedLlmModels : localLlmModels) : [...whisperStaticModels, ...fetchedVoskModels];
        const query = searchEntry ? searchEntry.text.trim().toLowerCase() : '';
        const activeTab = selectorViewStack ? (selectorViewStack.visible_child_name || 'all') : 'all';

        let targetGroup = modelsGroupAll;
        if (activeTab === 'installed') targetGroup = modelsGroupInstalled;
        else if (activeTab === 'downloading') targetGroup = modelsGroupDownloading;

        if (!targetGroup) return;

        const filteredModels = allModels.filter(m => {
            let modelKey = `${m.provider}:${m.id}`;
            let isDownloading = downloadingProgress.has(modelKey);
            let isCloud = (m.provider === 'openai_cloud' || m.provider === 'groq_cloud');
            let fileName = m.file || (m.id.includes(':') ? m.id.split(':')[1] : m.id);
            let isInstalled = isCloud || (!isDownloading && (installedSet.has(m.id) || installedSet.has(fileName) || installedSet.has(`${m.provider}-${m.id}`) || (m.provider === 'whisper' && installedSet.has(`whisper-${m.id}`))));

            if (activeTab === 'installed' && !isInstalled) return false;
            if (activeTab === 'downloading' && !isDownloading) return false;

            if (query.length > 0) {
                let text = `${m.name} ${m.id} ${m.provider} ${m.lang || ''} ${m.lang_text || ''} ${m.size_text}`.toLowerCase();
                if (!text.includes(query)) return false;
            }
            return true;
        });

        filteredModels.sort((a, b) => {
            let fileA = a.file || (a.id.includes(':') ? a.id.split(':')[1] : a.id);
            let fileB = b.file || (b.id.includes(':') ? b.id.split(':')[1] : b.id);
            let isCloudA = (a.provider === 'openai_cloud' || a.provider === 'groq_cloud');
            let isCloudB = (b.provider === 'openai_cloud' || b.provider === 'groq_cloud');

            let instA = isCloudA || installedSet.has(a.id) || installedSet.has(fileA) || installedSet.has(`${a.provider}-${a.id}`) || (a.provider === 'whisper' && installedSet.has(`whisper-${a.id}`));
            let instB = isCloudB || installedSet.has(b.id) || installedSet.has(fileB) || installedSet.has(`${b.provider}-${b.id}`) || (b.provider === 'whisper' && installedSet.has(`whisper-${b.id}`));

            if (instA && !instB) return -1;
            if (!instA && instB) return 1;
            return 0;
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

        let activeModelId = isLlm ? (settings.get_string('llm-model') || '') : (settings.get_string('stt-model') || '');
        let activeProvider = isLlm ? (settings.get_string('llm-mode') || 'local') : (settings.get_string('stt-provider') || 'vosk');

        filteredModels.forEach(m => {
            let modelKey = `${m.provider}:${m.id}`;
            let isDownloading = downloadingProgress.has(modelKey);
            let isCloud = (m.provider === 'openai_cloud' || m.provider === 'groq_cloud');
            let fileName = m.file || (m.id.includes(':') ? m.id.split(':')[1] : m.id);
            let isInstalled = isCloud || (!isDownloading && (installedSet.has(m.id) || installedSet.has(fileName) || installedSet.has(`${m.provider}-${m.id}`) || (m.provider === 'whisper' && installedSet.has(`whisper-${m.id}`))));
            let isCurrent = isLlm
                ? (activeProvider === 'local' && (activeModelId === m.id || activeModelId === fileName))
                : (activeProvider === m.provider && activeModelId === m.id);

            const row = new Adw.ActionRow({
                title: m.name,
                subtitle: m.subtitle,
                activatable: true
            });

            const icon = Gtk.Image.new_from_icon_name('vocal-assistant-symbolic');
            icon.valign = Gtk.Align.CENTER;
            icon.margin_end = 12;
            row.add_prefix(icon);

            let checkBtn = null;
            if (isInstalled) {
                checkBtn = new Gtk.CheckButton({
                    valign: Gtk.Align.CENTER,
                    active: isCurrent
                });

                if (isCurrent) {
                    row.add_css_class('accent');
                }

                row.add_prefix(checkBtn);
            }

            const selectModel = () => {
                if (checkBtn) checkBtn.active = true;
                if (isLlm) {
                    settings.set_string('llm-mode', 'local');
                    settings.set_string('llm-model', m.id);
                    renderModelList();
                    window.add_toast(new Adw.Toast({
                        title: _(`Model ${m.name} activated.`)
                    }));
                } else {
                    let currP = settings.get_string('stt-provider');
                    let currM = settings.get_string('stt-model');
                    if (currP !== m.provider || currM !== m.id) {
                        settings.set_string('stt-provider', m.provider);
                        settings.set_string('stt-model', m.id);
                        renderModelList();
                        const refresh = getRefreshCacheGroup();
                        if (typeof refresh === 'function') refresh();
                        window.add_toast(new Adw.Toast({
                            title: _(`Model ${m.name} activated.`)
                        }));
                    }
                }
            };

            const fallbackLocalDownload = () => {
                downloadingProgress.set(modelKey, 0);
                renderModelList();
                try {
                    let proc = new Gio.Subprocess({
                        argv: ['python3', `${path}/daemon/main.py`, '--download-only', '--provider', m.provider, '--model', m.id],
                        flags: Gio.SubprocessFlags.NONE
                    });
                    proc.init(null);
                    proc.wait_async(null, () => {
                        downloadingProgress.delete(modelKey);
                        GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
                            renderModelList();
                            const refresh = getRefreshCacheGroup();
                            if (typeof refresh === 'function') refresh();
                            return GLib.SOURCE_REMOVE;
                        });
                    });
                } catch (e) {
                    downloadingProgress.delete(modelKey);
                    renderModelList();
                }
            };

            const triggerDownload = (provider, modelId) => {
                let targetProvider = provider || m.provider;
                let targetModel = modelId || m.id;
                let key = `${targetProvider}:${targetModel}`;

                window.add_toast(new Adw.Toast({
                    title: _(`Started downloading ${targetModel}...`)
                }));

                downloadingProgress.set(key, 0);
                renderModelList();

                try {
                    Gio.DBus.session.call(
                        'org.local.VoiceAssistant',
                        '/org/local/VoiceAssistant',
                        'org.local.VoiceAssistant',
                        'DownloadModel',
                        new GLib.Variant('(ss)', [targetProvider, targetModel]),
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

            if (isDownloading) {
                let percent = downloadingProgress.get(modelKey) || 0;
                const progressBar = new Gtk.ProgressBar({
                    valign: Gtk.Align.CENTER,
                    show_text: true,
                    fraction: Math.min(1.0, Math.max(0.0, percent / 100.0)),
                    text: `${percent}%`,
                    hexpand: false
                });
                progressBar.set_size_request(120, -1);

                const cancelBtn = Gtk.Button.new_from_icon_name('edit-clear-symbolic');
                cancelBtn.valign = Gtk.Align.CENTER;
                cancelBtn.add_css_class('flat');
                cancelBtn.tooltip_text = _('Cancel download');

                downloadButtons.set(modelKey, { progressBar, cancelBtn });

                cancelBtn.connect('clicked', () => {
                    downloadingProgress.delete(modelKey);
                    downloadButtons.delete(modelKey);
                    renderModelList();
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
                            () => { }
                        );
                    } catch (e) { }
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
                let isRequiredVoskModel = (!isLlm) && (m.provider === 'vosk') && (
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
                    let sttPath = `${basePath}/stt/${folderName}`;
                    let legacyPath = `${basePath}/${folderName}`;
                    let targetPath = isLlm ? `${basePath}/llm/${m.id}` : (Gio.File.new_for_path(sttPath).query_exists(null) ? sttPath : legacyPath);
                    try {
                        let proc = new Gio.Subprocess({
                            argv: ['rm', '-rf', targetPath],
                            flags: Gio.SubprocessFlags.NONE
                        });
                        proc.init(null);
                        proc.wait_async(null, () => {
                            window.add_toast(new Adw.Toast({
                                title: _(`Model ${m.name} deleted.`)
                            }));
                            renderModelList();
                            const refresh = getRefreshCacheGroup();
                            if (typeof refresh === 'function') refresh();
                        });
                    } catch (e) { }
                });

                row.add_suffix(deleteBtn);
            } else {
                const dlBtn = Gtk.Button.new_from_icon_name('folder-download-symbolic');
                dlBtn.valign = Gtk.Align.CENTER;
                dlBtn.add_css_class('flat');
                dlBtn.tooltip_text = _('Download model');
                dlBtn.connect('clicked', () => triggerDownload(m.provider, m.id));
                row.add_suffix(dlBtn);
            }

            row.connect('activated', () => {
                if (isDownloading) return;
                if (isInstalled) {
                    selectModel();
                } else {
                    triggerDownload(m.provider, m.id);
                }
            });

            if (checkBtn) {
                checkBtn.connect('toggled', () => {
                    if (checkBtn.active && isInstalled) {
                        selectModel();
                    }
                });
            }

            targetGroup.add(row);
            activeModelGroupRows.push({ row, group: targetGroup });
        });
    };

    const openSttSelector = (contentNavigationView, splitView) => {
        currentSelectorMode = 'stt';
        if (modelSelectorPage) modelSelectorPage.title = _('Local STT Models');
        queryDownloadingModels(() => {
            renderModelList();
        });
        if (contentNavigationView && modelSelectorPage) contentNavigationView.push(modelSelectorPage);
        if (splitView) splitView.set_show_content(true);
    };

    const openLlmSelector = (contentNavigationView, splitView) => {
        currentSelectorMode = 'llm';
        if (modelSelectorPage) modelSelectorPage.title = _('Local LLM Models');
        fetchLlmModelsOnline();
        queryDownloadingModels(() => {
            renderModelList();
        });
        if (contentNavigationView && modelSelectorPage) contentNavigationView.push(modelSelectorPage);
        if (splitView) splitView.set_show_content(true);
    };

    const startDownloadExternal = (provider, modelId) => {
        window.add_toast(new Adw.Toast({
            title: _(`Started downloading ${modelId}...`)
        }));
        let key = `${provider}:${modelId}`;
        downloadingProgress.set(key, 0);
        renderModelList();

        try {
            Gio.DBus.session.call(
                'org.local.VoiceAssistant',
                '/org/local/VoiceAssistant',
                'org.local.VoiceAssistant',
                'DownloadModel',
                new GLib.Variant('(ss)', [provider, modelId]),
                null,
                Gio.DBusCallFlags.NONE,
                -1,
                null,
                (conn, res) => {
                    try { conn.call_finish(res); } catch (e) { }
                }
            );
        } catch (err) { }
    };

    return {
        renderModelList,
        queryDownloadingModels,
        openSttSelector,
        openLlmSelector,
        fetchLlmModelsOnline,
        startDownload: startDownloadExternal
    };
}
