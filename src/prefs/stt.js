import Gio from 'gi://Gio';

export function setupSttPage(builder, settings, openSttSelector, _) {
    const sttModeLocalRow = builder.get_object('stt_mode_local_row');
    const sttModeLocalRadio = builder.get_object('stt_mode_local_radio');
    const sttModeCloudRow = builder.get_object('stt_mode_cloud_row');
    const sttModeCloudRadio = builder.get_object('stt_mode_cloud_radio');

    const sttLocalGroup = builder.get_object('stt_local_group');
    const sttCloudGroup = builder.get_object('stt_cloud_group');
    const sttCloudOpenaiRow = builder.get_object('stt_cloud_openai_row');
    const sttCloudOpenaiRadio = builder.get_object('stt_cloud_openai_radio');
    const sttCloudGroqRow = builder.get_object('stt_cloud_groq_row');
    const sttCloudGroqRadio = builder.get_object('stt_cloud_groq_radio');
    const sttCloudApiKeyRow = builder.get_object('stt_cloud_api_key_row');
    const sttCloudModelRow = builder.get_object('stt_cloud_model_row');

    const currentModelRow = builder.get_object('current_model_row');
    const openModelSelectorBtn = builder.get_object('open_model_selector_btn');
    const whisperHardwareGroup = builder.get_object('whisper_hardware_group');
    const hwCpuRow = builder.get_object('hw_cpu_row');
    const hwCpuRadio = builder.get_object('hw_cpu_radio');
    const hwCudaRow = builder.get_object('hw_cuda_row');
    const hwCudaRadio = builder.get_object('hw_cuda_radio');

    if (hwCpuRadio && hwCudaRadio) {
        let currentHw = settings.get_string('stt-hardware') || 'cpu';
        if (currentHw === 'cuda') hwCudaRadio.active = true;
        else hwCpuRadio.active = true;

        const selectHw = (hwId) => {
            if (hwId === 'cuda') hwCudaRadio.active = true;
            else hwCpuRadio.active = true;
            settings.set_string('stt-hardware', hwId);
        };

        if (hwCpuRow) hwCpuRow.connect('activated', () => selectHw('cpu'));
        if (hwCudaRow) hwCudaRow.connect('activated', () => selectHw('cuda'));
        hwCpuRadio.connect('toggled', () => { if (hwCpuRadio.active) settings.set_string('stt-hardware', 'cpu'); });
        hwCudaRadio.connect('toggled', () => { if (hwCudaRadio.active) settings.set_string('stt-hardware', 'cuda'); });
    }

    if (sttCloudApiKeyRow) settings.bind('llm-api-key', sttCloudApiKeyRow, 'text', Gio.SettingsBindFlags.DEFAULT);
    if (sttCloudModelRow) settings.bind('stt-model', sttCloudModelRow, 'text', Gio.SettingsBindFlags.DEFAULT);

    const updateSttModeUI = () => {
        let provider = settings.get_string('stt-provider') || 'vosk';
        let isCloud = (provider === 'openai_cloud' || provider === 'groq_cloud');

        if (isCloud) {
            if (sttModeCloudRadio) sttModeCloudRadio.active = true;
            if (sttLocalGroup) sttLocalGroup.visible = false;
            if (sttCloudGroup) sttCloudGroup.visible = true;
            if (whisperHardwareGroup) whisperHardwareGroup.visible = false;

            if (provider === 'groq_cloud') {
                if (sttCloudGroqRadio) sttCloudGroqRadio.active = true;
            } else {
                if (sttCloudOpenaiRadio) sttCloudOpenaiRadio.active = true;
            }
        } else {
            if (sttModeLocalRadio) sttModeLocalRadio.active = true;
            if (sttLocalGroup) sttLocalGroup.visible = true;
            if (sttCloudGroup) sttCloudGroup.visible = false;
            if (whisperHardwareGroup) whisperHardwareGroup.visible = (provider === 'whisper');
        }

        let model = settings.get_string('stt-model') || 'vosk-model-small-it-0.22';
        let providerDisplay = 'Vosk';
        if (provider === 'whisper') providerDisplay = 'Whisper';
        else if (provider === 'openai_cloud') providerDisplay = 'OpenAI Cloud STT';
        else if (provider === 'groq_cloud') providerDisplay = 'Groq Cloud STT';

        if (currentModelRow) currentModelRow.subtitle = `${providerDisplay} • ${model}`;
    };

    if (sttModeLocalRow) {
        sttModeLocalRow.connect('activated', () => {
            if (sttModeLocalRadio) sttModeLocalRadio.active = true;
            let currentP = settings.get_string('stt-provider');
            if (currentP === 'openai_cloud' || currentP === 'groq_cloud') {
                settings.set_string('stt-provider', 'vosk');
            }
            updateSttModeUI();
        });
    }

    if (sttModeCloudRow) {
        sttModeCloudRow.connect('activated', () => {
            if (sttModeCloudRadio) sttModeCloudRadio.active = true;
            let currentP = settings.get_string('stt-provider');
            if (currentP !== 'openai_cloud' && currentP !== 'groq_cloud') {
                settings.set_string('stt-provider', 'openai_cloud');
                settings.set_string('stt-model', 'whisper-1');
            }
            updateSttModeUI();
        });
    }

    if (sttModeLocalRadio) {
        sttModeLocalRadio.connect('toggled', () => {
            if (sttModeLocalRadio.active) {
                let currentP = settings.get_string('stt-provider');
                if (currentP === 'openai_cloud' || currentP === 'groq_cloud') {
                    settings.set_string('stt-provider', 'vosk');
                }
                updateSttModeUI();
            }
        });
    }

    if (sttModeCloudRadio) {
        sttModeCloudRadio.connect('toggled', () => {
            if (sttModeCloudRadio.active) {
                let currentP = settings.get_string('stt-provider');
                if (currentP !== 'openai_cloud' && currentP !== 'groq_cloud') {
                    settings.set_string('stt-provider', 'openai_cloud');
                    settings.set_string('stt-model', 'whisper-1');
                }
                updateSttModeUI();
            }
        });
    }

    if (sttCloudOpenaiRow) {
        sttCloudOpenaiRow.connect('activated', () => {
            if (sttCloudOpenaiRadio) sttCloudOpenaiRadio.active = true;
            settings.set_string('stt-provider', 'openai_cloud');
            settings.set_string('stt-model', 'whisper-1');
            updateSttModeUI();
        });
    }

    if (sttCloudGroqRow) {
        sttCloudGroqRow.connect('activated', () => {
            if (sttCloudGroqRadio) sttCloudGroqRadio.active = true;
            settings.set_string('stt-provider', 'groq_cloud');
            settings.set_string('stt-model', 'whisper-large-v3');
            updateSttModeUI();
        });
    }

    if (sttCloudOpenaiRadio) {
        sttCloudOpenaiRadio.connect('toggled', () => {
            if (sttCloudOpenaiRadio.active) {
                settings.set_string('stt-provider', 'openai_cloud');
                settings.set_string('stt-model', 'whisper-1');
                updateSttModeUI();
            }
        });
    }

    if (sttCloudGroqRadio) {
        sttCloudGroqRadio.connect('toggled', () => {
            if (sttCloudGroqRadio.active) {
                settings.set_string('stt-provider', 'groq_cloud');
                settings.set_string('stt-model', 'whisper-large-v3');
                updateSttModeUI();
            }
        });
    }

    if (currentModelRow && openSttSelector) currentModelRow.connect('activated', openSttSelector);
    if (openModelSelectorBtn && openSttSelector) openModelSelectorBtn.connect('clicked', openSttSelector);

    updateSttModeUI();

    settings.connect('changed::stt-provider', updateSttModeUI);
    settings.connect('changed::stt-model', updateSttModeUI);

    return { updateSttModeUI };
}
