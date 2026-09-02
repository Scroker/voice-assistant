import Gio from 'gi://Gio';

export function setupTtsPage(builder, settings, _) {
    const ttsEnableRow = builder.get_object('tts_enable_row');
    const ttsEnginePiperRow = builder.get_object('tts_engine_piper_row');
    const ttsEnginePiperRadio = builder.get_object('tts_engine_piper_radio');
    const ttsEngineEspeakRow = builder.get_object('tts_engine_espeak_row');
    const ttsEngineEspeakRadio = builder.get_object('tts_engine_espeak_radio');
    const ttsEngineOpenaiRow = builder.get_object('tts_engine_openai_row');
    const ttsEngineOpenaiRadio = builder.get_object('tts_engine_openai_radio');
    const ttsEngineSystemRow = builder.get_object('tts_engine_system_row');
    const ttsEngineSystemRadio = builder.get_object('tts_engine_system_radio');
    const ttsVoiceRow = builder.get_object('tts_voice_row');

    if (ttsEnableRow) settings.bind('tts-enabled', ttsEnableRow, 'active', Gio.SettingsBindFlags.DEFAULT);
    if (ttsVoiceRow) settings.bind('tts-voice', ttsVoiceRow, 'text', Gio.SettingsBindFlags.DEFAULT);

    const ttsRadios = [
        { engine: 'piper', row: ttsEnginePiperRow, radio: ttsEnginePiperRadio },
        { engine: 'espeak', row: ttsEngineEspeakRow, radio: ttsEngineEspeakRadio },
        { engine: 'openai', row: ttsEngineOpenaiRow, radio: ttsEngineOpenaiRadio },
        { engine: 'system', row: ttsEngineSystemRow, radio: ttsEngineSystemRadio }
    ].filter(r => r.row && r.radio);

    let currentTts = settings.get_string('tts-provider') || settings.get_string('tts-engine') || 'piper';
    let initialTts = ttsRadios.find(r => r.engine === currentTts) || ttsRadios[0];
    if (initialTts && initialTts.radio) initialTts.radio.active = true;

    ttsRadios.forEach(({ engine, row, radio }) => {
        row.connect('activated', () => {
            radio.active = true;
        });
        radio.connect('toggled', () => {
            if (radio.active) {
                settings.set_string('tts-engine', engine);
                settings.set_string('tts-provider', engine);
            }
        });
    });
}
