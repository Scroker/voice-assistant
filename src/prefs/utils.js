import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

export function getModelsPath(settings) {
    let customPath = settings.get_string('models-dir');
    if (customPath && customPath.trim().length > 0) {
        if (customPath.startsWith('~/')) {
            return GLib.get_home_dir() + customPath.substring(1);
        }
        return customPath;
    }
    return GLib.get_home_dir() + '/.local/share/voice-assistant/models';
}

export function formatPathForDisplay(path) {
    const home = GLib.get_home_dir();
    if (path.startsWith(home)) {
        return '~' + path.substring(home.length);
    }
    return path;
}

export function getDirSize(dirPath) {
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
}
