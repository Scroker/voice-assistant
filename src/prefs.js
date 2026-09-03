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
import { ExtensionPreferences } from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

/**
 * Preferences are managed entirely by the GUI Python process (settings_window.py).
 * This class delegates to it so that settings logic lives in one place only.
 */
export default class VoiceAssistantPreferences extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        const guiScript = GLib.build_filenamev([this.path, 'gui', 'start.sh']);

        try {
            Gio.Subprocess.new(
                ['bash', guiScript, '--open-settings'],
                Gio.SubprocessFlags.NONE
            );
        } catch (e) {
            console.error('[VoiceAssistant] Failed to open settings via GUI:', e.message);
        }

        // Close the empty shell prefs window once the GUI window is ready.
        GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
            window.close();
            return GLib.SOURCE_REMOVE;
        });
    }
}
