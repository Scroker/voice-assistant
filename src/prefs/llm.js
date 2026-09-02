import Gio from 'gi://Gio';

export function setupLlmPage(builder, settings, openLlmSelector, startDownload, _) {
    const llmEnableRow = builder.get_object('llm_enable_row');
    const llmModeLocalRow = builder.get_object('llm_mode_local_row');
    const llmModeLocalRadio = builder.get_object('llm_mode_local_radio');
    const llmModeOllamaRow = builder.get_object('llm_mode_ollama_row');
    const llmModeOllamaRadio = builder.get_object('llm_mode_ollama_radio');
    const llmModeOpenaiRow = builder.get_object('llm_mode_openai_row');
    const llmModeOpenaiRadio = builder.get_object('llm_mode_openai_radio');
    const llmModeAnthropicRow = builder.get_object('llm_mode_anthropic_row');
    const llmModeAnthropicRadio = builder.get_object('llm_mode_anthropic_radio');
    const llmModeCustomRow = builder.get_object('llm_mode_custom_row');
    const llmModeCustomRadio = builder.get_object('llm_mode_custom_radio');

    const llmLocalGroup = builder.get_object('llm_local_group');
    const llmHfCustomGroup = builder.get_object('llm_hf_custom_group');
    const customHfEntryRow = builder.get_object('custom_hf_entry_row');
    const currentLlmModelRow = builder.get_object('current_llm_model_row');
    const openLlmModelSelectorBtn = builder.get_object('open_llm_model_selector_btn');

    if (customHfEntryRow && typeof startDownload === 'function') {
        customHfEntryRow.connect('apply', (entry) => {
            let text = entry.text ? entry.text.trim() : '';
            if (text.length > 0) {
                startDownload('llm', text);
                entry.text = '';
            }
        });
    }

    const llmApiKeyRow = builder.get_object('llm_api_key_row');
    const llmSystemPromptRow = builder.get_object('llm_system_prompt_row');
    const llmUrlRow = builder.get_object('llm_url_row');
    const llmModelRow = builder.get_object('llm_model_row');

    if (llmEnableRow) settings.bind('llm-enabled', llmEnableRow, 'active', Gio.SettingsBindFlags.DEFAULT);
    if (llmApiKeyRow) settings.bind('llm-api-key', llmApiKeyRow, 'text', Gio.SettingsBindFlags.DEFAULT);
    if (llmSystemPromptRow) settings.bind('llm-system-prompt', llmSystemPromptRow, 'text', Gio.SettingsBindFlags.DEFAULT);
    if (llmUrlRow) settings.bind('llm-endpoint', llmUrlRow, 'text', Gio.SettingsBindFlags.DEFAULT);
    if (llmModelRow) settings.bind('llm-model', llmModelRow, 'text', Gio.SettingsBindFlags.DEFAULT);

    const updateActiveLlmModelSubtitle = () => {
        let mode = settings.get_string('llm-mode') || 'local';
        let model = settings.get_string('llm-model') || 'Llama-3.2-1B-Instruct-Q4_K_M.gguf';
        if (currentLlmModelRow) {
            let modeLabel = (mode === 'local') ? 'Local GGUF' : (mode === 'ollama' ? 'Ollama' : (mode === 'openai' ? 'OpenAI' : (mode === 'anthropic' ? 'Anthropic' : 'Custom')));
            currentLlmModelRow.subtitle = `${modeLabel} • ${model}`;
        }
    };
    updateActiveLlmModelSubtitle();
    settings.connect('changed::llm-mode', updateActiveLlmModelSubtitle);
    settings.connect('changed::llm-model', updateActiveLlmModelSubtitle);

    const llmRadios = [
        { mode: 'local', row: llmModeLocalRow, radio: llmModeLocalRadio },
        { mode: 'ollama', row: llmModeOllamaRow, radio: llmModeOllamaRadio },
        { mode: 'openai', row: llmModeOpenaiRow, radio: llmModeOpenaiRadio },
        { mode: 'anthropic', row: llmModeAnthropicRow, radio: llmModeAnthropicRadio },
        { mode: 'http', row: llmModeCustomRow, radio: llmModeCustomRadio }
    ].filter(r => r.row && r.radio);

    let currentLlmMode = settings.get_string('llm-mode') || 'local';
    let initialLlm = llmRadios.find(r => r.mode === currentLlmMode) || llmRadios[0];
    if (initialLlm && initialLlm.radio) initialLlm.radio.active = true;

    const updateLlmModeVisibility = (mode) => {
        if (llmLocalGroup) llmLocalGroup.visible = (mode === 'local');
        if (llmHfCustomGroup) llmHfCustomGroup.visible = (mode === 'local');
        if (llmApiKeyRow) llmApiKeyRow.visible = (mode === 'openai' || mode === 'anthropic' || mode === 'http');
        if (llmUrlRow) llmUrlRow.visible = (mode === 'ollama' || mode === 'http');
        if (llmModelRow) llmModelRow.visible = (mode !== 'local');
    };
    updateLlmModeVisibility(currentLlmMode);

    llmRadios.forEach(({ mode, row, radio }) => {
        row.connect('activated', () => {
            radio.active = true;
        });
        radio.connect('toggled', () => {
            if (radio.active) {
                settings.set_string('llm-mode', mode);
                updateLlmModeVisibility(mode);
            }
        });
    });

    if (currentLlmModelRow && openLlmSelector) currentLlmModelRow.connect('activated', openLlmSelector);
    if (openLlmModelSelectorBtn && openLlmSelector) openLlmModelSelectorBtn.connect('clicked', openLlmSelector);

    return { updateActiveLlmModelSubtitle, updateLlmModeVisibility };
}
