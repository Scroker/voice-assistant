import Gio from 'gi://Gio';
import Gtk from 'gi://Gtk';
import Adw from 'gi://Adw';
import { getModelsPath, formatPathForDisplay, getDirSize } from './utils.js';

export function setupGeneralPage(builder, settings, window, _, onModelsDirChanged) {
    // 1. General Settings
    const enableSwitchRow = builder.get_object('enable_switch_row');
    if (enableSwitchRow) settings.bind('enabled', enableSwitchRow, 'active', Gio.SettingsBindFlags.DEFAULT);

    // Language radio buttons (wakeword_page)
    const langItRadio = builder.get_object('lang_it_radio');
    const langEnRadio = builder.get_object('lang_en_radio');
    if (langItRadio && langEnRadio) {
        const currentLang = settings.get_string('language') || 'it';
        if (currentLang === 'en') langEnRadio.set_active(true);
        else langItRadio.set_active(true);

        const onLangChanged = () => {
            const newLang = langEnRadio.get_active() ? 'en' : 'it';
            settings.set_string('language', newLang);
            if (typeof onModelsDirChanged === 'function') onModelsDirChanged();
        };
        langItRadio.connect('notify::active', onLangChanged);
        langEnRadio.connect('notify::active', onLangChanged);
    }

    // Wakeword engine radio buttons
    const wwEngineVoskRadio = builder.get_object('ww_engine_vosk_radio');
    const wwEngineOwwRadio = builder.get_object('ww_engine_oww_radio');
    const wwEngineSherpaRadio = builder.get_object('ww_engine_sherpa_radio');

    // Wakeword config widgets
    const wakewordRow = builder.get_object('wakeword_row');
    const sherpaModelDirRow = builder.get_object('sherpa_model_dir_row');
    const owwKeywordGroup = builder.get_object('oww_keyword_group');

    const applyEngineVisibility = (engine) => {
        const isOww = engine === 'openwakeword';
        const isSherpa = engine === 'sherpa-onnx';
        if (wakewordRow) wakewordRow.set_visible(!isOww);
        if (owwKeywordGroup) owwKeywordGroup.set_visible(isOww);
        if (sherpaModelDirRow) sherpaModelDirRow.set_visible(isSherpa);
    };

    const getSelectedEngine = () => {
        if (wwEngineOwwRadio?.get_active()) return 'openwakeword';
        if (wwEngineSherpaRadio?.get_active()) return 'sherpa-onnx';
        return 'vosk';
    };

    if (wwEngineVoskRadio && wwEngineOwwRadio && wwEngineSherpaRadio) {
        const currentEngine = settings.get_string('wakeword-engine') || 'vosk';
        if (currentEngine === 'openwakeword') wwEngineOwwRadio.set_active(true);
        else if (currentEngine === 'sherpa-onnx') wwEngineSherpaRadio.set_active(true);
        else wwEngineVoskRadio.set_active(true);
        applyEngineVisibility(currentEngine);

        const onEngineChanged = () => {
            if (!wwEngineVoskRadio.get_active() && !wwEngineOwwRadio.get_active() && !wwEngineSherpaRadio.get_active()) return;
            const newEngine = getSelectedEngine();
            settings.set_string('wakeword-engine', newEngine);
            applyEngineVisibility(newEngine);
        };
        wwEngineVoskRadio.connect('notify::active', onEngineChanged);
        wwEngineOwwRadio.connect('notify::active', onEngineChanged);
        wwEngineSherpaRadio.connect('notify::active', onEngineChanged);
    }

    if (wakewordRow) settings.bind('wakeword', wakewordRow, 'text', Gio.SettingsBindFlags.DEFAULT);
    if (sherpaModelDirRow) settings.bind('sherpa-ww-model-dir', sherpaModelDirRow, 'text', Gio.SettingsBindFlags.DEFAULT);

    // OWW keyword radio buttons
    const OWW_RADIOS = [
        { id: 'oww_alexa_radio', value: 'alexa' },
        { id: 'oww_hey_jarvis_radio', value: 'hey_jarvis' },
        { id: 'oww_hey_mycroft_radio', value: 'hey_mycroft' },
        { id: 'oww_hey_rhasspy_radio', value: 'hey_rhasspy' },
    ];
    const owwRadioWidgets = OWW_RADIOS.map(r => ({ ...r, widget: builder.get_object(r.id) }));
    const currentOwwModel = settings.get_string('oww-model') || 'alexa';
    owwRadioWidgets.forEach(r => {
        if (!r.widget) return;
        if (r.value === currentOwwModel) r.widget.set_active(true);
        r.widget.connect('notify::active', () => {
            if (r.widget.get_active()) settings.set_string('oww-model', r.value);
        });
    });

    // 2. Storage & Cache Management
    const modelsPathRow = builder.get_object('models_path_row');
    const choosePathBtn = builder.get_object('choose_path_btn');
    const resetPathBtn = builder.get_object('reset_path_btn');
    const cacheGroup = builder.get_object('cache_group');
    const cleanUnusedBtn = builder.get_object('clean_unused_btn');

    let activeCacheRows = [];

    const updateModelsPathDisplay = () => {
        if (modelsPathRow) {
            const currentPath = getModelsPath(settings);
            modelsPathRow.subtitle = formatPathForDisplay(currentPath);
        }
    };
    updateModelsPathDisplay();

    const refreshCacheGroup = () => {
        if (!cacheGroup) return;

        for (const r of activeCacheRows) {
            try {
                cacheGroup.remove(r);
            } catch (e) { }
        }
        activeCacheRows = [];

        const currentModelsPath = getModelsPath(settings);
        cacheGroup.set_description(_(`Manage downloaded models in ${formatPathForDisplay(currentModelsPath)}`));

        let currentSttModel = settings.get_string('stt-model') || '';
        let currentSttProvider = settings.get_string('stt-provider') || 'vosk';
        let activeSttFolderName = currentSttProvider === 'whisper' ? (currentSttModel.startsWith('whisper-') ? currentSttModel : `whisper-${currentSttModel}`) : currentSttModel;

        let activeLlmMode = settings.get_string('llm-mode') || 'local';
        let activeLlmModel = settings.get_string('llm-model') || '';
        if (activeLlmModel.includes(':')) activeLlmModel = activeLlmModel.split(':')[1];

        let activeTtsVoice = settings.get_string('tts-voice') || '';

        let items = [];

        const scanSubdir = (subName, category) => {
            const dirPath = currentModelsPath + '/' + subName;
            try {
                let dir = Gio.File.new_for_path(dirPath);
                let enumerator = dir.enumerate_children('standard::name,standard::type', Gio.FileQueryInfoFlags.NONE, null);
                let info;
                while ((info = enumerator.next_file(null)) !== null) {
                    let name = info.get_name();
                    if (name.startsWith('.')) continue;

                    const fullPath = dirPath + '/' + name;
                    const size = getDirSize(fullPath);
                    let isActive = false;

                    if (category === 'STT') {
                        isActive = (name === activeSttFolderName || name === currentSttModel);
                    } else if (category === 'LLM') {
                        isActive = (activeLlmMode === 'local' && (name === activeLlmModel || activeLlmModel.endsWith(name)));
                    } else if (category === 'TTS') {
                        isActive = (activeTtsVoice && name.includes(activeTtsVoice));
                    }

                    items.push({ category, name, fullPath, size, isActive });
                }
            } catch (e) { }
        };

        scanSubdir('stt', 'STT');
        scanSubdir('llm', 'LLM');
        scanSubdir('tts', 'TTS');

        try {
            let rootDir = Gio.File.new_for_path(currentModelsPath);
            let enumerator = rootDir.enumerate_children('standard::name,standard::type', Gio.FileQueryInfoFlags.NONE, null);
            let info;
            while ((info = enumerator.next_file(null)) !== null) {
                let name = info.get_name();
                if (name.startsWith('.') || name === 'stt' || name === 'llm' || name === 'tts') continue;

                const fullPath = currentModelsPath + '/' + name;
                const size = getDirSize(fullPath);
                let isActive = (name === activeSttFolderName || name === currentSttModel);
                items.push({ category: 'STT', name, fullPath, size, isActive });
            }
        } catch (e) { }

        let activeLang = settings.get_string('language') || 'it';
        let unusedItems = items.filter(i => {
            let isRequiredVosk = (i.category === 'STT') && (
                (activeLang === 'it' && i.name === 'vosk-model-small-it-0.22') ||
                (activeLang !== 'it' && i.name === 'vosk-model-small-en-us-0.15')
            );
            return !i.isActive && !isRequiredVosk;
        });

        if (cleanUnusedBtn) cleanUnusedBtn.sensitive = (unusedItems.length > 0);

        if (items.length === 0) {
            const emptyRow = new Adw.ActionRow({
                title: _('No downloaded models'),
                subtitle: _('Models will be downloaded automatically on first use')
            });
            cacheGroup.add(emptyRow);
            activeCacheRows.push(emptyRow);
        } else {
            items.sort((a, b) => a.category.localeCompare(b.category) || a.name.localeCompare(b.name));

            for (const item of items) {
                const row = new Adw.ActionRow({
                    title: `[${item.category}] ${item.name}`,
                    subtitle: item.size
                });

                if (item.isActive) {
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

    if (choosePathBtn) {
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
                        if (typeof onModelsDirChanged === 'function') onModelsDirChanged();
                    }
                }
                chooser.destroy();
            });
            chooser.show();
        });
    }

    if (resetPathBtn) {
        resetPathBtn.connect('clicked', () => {
            settings.reset('models-dir');
            updateModelsPathDisplay();
            refreshCacheGroup();
            if (typeof onModelsDirChanged === 'function') onModelsDirChanged();
        });
    }

    if (cleanUnusedBtn) {
        cleanUnusedBtn.connect('clicked', () => {
            const currentModelsPath = getModelsPath(settings);
            let currentSttModel = settings.get_string('stt-model') || '';
            let currentSttProvider = settings.get_string('stt-provider') || 'vosk';
            let activeSttFolderName = currentSttProvider === 'whisper' ? (currentSttModel.startsWith('whisper-') ? currentSttModel : `whisper-${currentSttModel}`) : currentSttModel;

            let activeLlmMode = settings.get_string('llm-mode') || 'local';
            let activeLlmModel = settings.get_string('llm-model') || '';
            if (activeLlmModel.includes(':')) activeLlmModel = activeLlmModel.split(':')[1];

            let activeTtsVoice = settings.get_string('tts-voice') || '';
            let activeLang = settings.get_string('language') || 'it';

            let countRemoved = 0;

            const cleanDir = (subName, category) => {
                const dirPath = currentModelsPath + (subName ? '/' + subName : '');
                try {
                    let dir = Gio.File.new_for_path(dirPath);
                    let enumerator = dir.enumerate_children('standard::name,standard::type', Gio.FileQueryInfoFlags.NONE, null);
                    let info;
                    while ((info = enumerator.next_file(null)) !== null) {
                        let name = info.get_name();
                        if (name.startsWith('.')) continue;
                        if (!subName && (name === 'stt' || name === 'llm' || name === 'tts')) continue;

                        let isActive = false;
                        if (category === 'STT') {
                            isActive = (name === activeSttFolderName || name === currentSttModel);
                        } else if (category === 'LLM') {
                            isActive = (activeLlmMode === 'local' && (name === activeLlmModel || activeLlmModel.endsWith(name)));
                        } else if (category === 'TTS') {
                            isActive = (activeTtsVoice && name.includes(activeTtsVoice));
                        }

                        let isRequiredVosk = (category === 'STT') && (
                            (activeLang === 'it' && name === 'vosk-model-small-it-0.22') ||
                            (activeLang !== 'it' && name === 'vosk-model-small-en-us-0.15')
                        );

                        if (!isActive && !isRequiredVosk) {
                            let targetPath = dirPath + '/' + name;
                            let proc = new Gio.Subprocess({
                                argv: ['rm', '-rf', targetPath],
                                flags: Gio.SubprocessFlags.NONE
                            });
                            proc.init(null);
                            countRemoved++;
                        }
                    }
                } catch (e) { }
            };

            cleanDir('stt', 'STT');
            cleanDir('llm', 'LLM');
            cleanDir('tts', 'TTS');
            cleanDir('', 'STT');

            refreshCacheGroup();
            if (typeof onModelsDirChanged === 'function') onModelsDirChanged();
            window.add_toast(new Adw.Toast({
                title: countRemoved > 0
                    ? _('Unused models removed successfully!')
                    : _('No unused models to remove.')
            }));
        });
    }

    refreshCacheGroup();

    return { refreshCacheGroup };
}
