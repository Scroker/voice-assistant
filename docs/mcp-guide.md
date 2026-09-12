# Guida Completa all'Integrazione Model Context Protocol (MCP)

Questa guida documenta l'architettura **Model Context Protocol (MCP)** basata su [gnome-mcp-server](https://github.com/bilelmoussaoui/gnome-mcp-server) e i server esterni integrati nel Voice Assistant per GNOME Shell.

---

## 💡 Cos'è il Model Context Protocol (MCP) nel Voice Assistant?

MCP è uno standard aperto che consente al modello di linguaggio (LLM) e al motore della pipeline vocale di interagire direttamente con il desktop GNOME e con servizi esterni.

L'architettura MCP del Voice Assistant comprende:
1. **gnome-mcp-server (Stdio JSON-RPC 2.0)**: Server MCP nativo per GNOME scritto in Rust, sviluppato da Bilal Elmoussaoui, che espone il controllo del desktop GNOME direttamente tramite protocollo MCP standard.
2. **Fast-Path Offline (<10ms)**: Esecuzione deterministica istantanea per i comandi vocali comuni (volume, tema, app, media) mappati direttamente sui tool di `gnome-mcp-server`.
3. **Dynamic Prompt Injection**: Iniezione automatica degli schemi dei tool di `gnome-mcp-server` e degli eventuali server esterni nel `system_prompt` dell'LLM.
4. **Adapter di Retrocompatibilità**: Livello di traduzione automatico in `MCPManager` che traduce chiamate legacy (es. `system_volume`, `dark_mode`, `app_launcher`, `system_media`) negli schemi ufficiali di `gnome-mcp-server`.
5. **Installazione Automatica delle Dipendenze**: Rilevamento e installazione automatica di `cargo` tramite PackageKit D-Bus o gestore pacchetti di sistema (DNF5, APT, Pacman, Zypper), con compilazione automatica di `gnome-mcp-server`.
6. **Marketplace Smithery & Server Esterni**: Supporto a server MCP di terze parti configurati in `~/.config/voice-assistant/mcp_servers.json`.

---

## 🛠️ I 10 Tool di `gnome-mcp-server`

`gnome-mcp-server` espone 10 strumenti specializzati per l'ambiente desktop GNOME:

### 1. `set_volume`
- **Descrizione**: Regola e muta il volume audio di sistema.
- **Parametri**:
  - `volume` (number, opzionale): Livello del volume assoluto o relativo (`0-100`).
  - `mute` (boolean, opzionale): `true` per silenziare, `false` per riattivare.
  - `relative` (boolean, opzionale): Se `true`, il valore indica una variazione relativa.
  - `direction` (string, opzionale): `"up"` o `"down"` per passo predefinito.
- **Esempio JSON**:
  ```json
  {"tool": "set_volume", "args": {"volume": 70}}
  ```

### 2. `quick_settings`
- **Descrizione**: Attiva o disattiva impostazioni booleane di GNOME (Wi-Fi, Bluetooth, Night Light, Dark Style, Do Not Disturb).
- **Parametri**:
  - `setting` (string, obbligatorio): `"wifi" | "bluetooth" | "night_light" | "do_not_disturb" | "dark_style"`
  - `enabled` (boolean, obbligatorio): `true` per attivare, `false` per disattivare.
- **Esempio JSON**:
  ```json
  {"tool": "quick_settings", "args": {"setting": "dark_style", "enabled": true}}
  ```

### 3. `launch_application`
- **Descrizione**: Avvia un'applicazione desktop per nome o eseguibile.
- **Parametri**:
  - `app_name` (string, obbligatorio): Nome dell'applicazione (es. `"firefox"`, `"nautilus"`, `"terminal"`).
- **Esempio JSON**:
  ```json
  {"tool": "launch_application", "args": {"app_name": "firefox"}}
  ```

### 4. `media_control`
- **Descrizione**: Controlla la riproduzione multimediale (MPRIS).
- **Parametri**:
  - `action` (string, obbligatorio): `"play" | "pause" | "play_pause" | "stop" | "next" | "previous"`
  - `player` (string, opzionale): Nome specifico del player da controllare.
- **Esempio JSON**:
  ```json
  {"tool": "media_control", "args": {"action": "play_pause"}}
  ```

### 5. `send_notification`
- **Descrizione**: Invia una notifica desktop visiva.
- **Parametri**:
  - `summary` (string, obbligatorio): Titolo della notifica.
  - `body` (string, obbligatorio): Testo della notifica.
- **Esempio JSON**:
  ```json
  {"tool": "send_notification", "args": {"summary": "Promemoria", "body": "Riunione alle 15:00"}}
  ```

### 6. `open_file`
- **Descrizione**: Apre un file locale o un URL con l'applicazione predefinita del desktop.
- **Parametri**:
  - `path` (string, obbligatorio): Percorso del file o URL.
- **Esempio JSON**:
  ```json
  {"tool": "open_file", "args": {"path": "/home/user/documento.pdf"}}
  ```

### 7. `set_wallpaper`
- **Descrizione**: Imposta lo sfondo del desktop a partire da un file immagine locale.
- **Parametri**:
  - `image_path` (string, obbligatorio): Percorso assoluto dell'immagine.
- **Esempio JSON**:
  ```json
  {"tool": "set_wallpaper", "args": {"image_path": "/home/user/Pictures/wallpaper.jpg"}}
  ```

### 8. `take_screenshot`
- **Descrizione**: Acquisisce una schermata del desktop.
- **Parametri**:
  - `interactive` (boolean, opzionale): Se `true`, mostra l'interfaccia interattiva di selezione area di GNOME.
- **Esempio JSON**:
  ```json
  {"tool": "take_screenshot", "args": {"interactive": false}}
  ```

### 9. `window_management`
- **Descrizione**: Gestisce finestre e aree di lavoro (focus, minimizza, massimizza, snap, sposta su workspace).
- **Parametri**:
  - `action` (string, obbligatorio): `"list" | "focus" | "close" | "minimize" | "maximize" | "switch_workspace" | "move_to_workspace" | "snap"`
  - `window_id` (string, opzionale): Identificatore della finestra.
  - `workspace` (integer, opzionale): Indice del workspace (0-based).
  - `position` (string, opzionale): `"left" | "right"` per azione snap.

### 10. `keyring_management`
- **Descrizione**: Gestisce credenziali e segreti nel GNOME Keyring in modo sicuro.
- **Parametri**:
  - `action` (string, obbligatorio): `"store" | "retrieve" | "delete"`
  - `label` (string, opzionale): Etichetta del segreto.
  - `secret` (string, opzionale): Valore del segreto.
  - `attributes` (string, opzionale): Attributi JSON associati.

---

## 🔄 Livello di Compatibilità e Retrocompatibilità

Per garantire che le skill esistenti, i prompt salvati e le integrazioni storiche continuino a funzionare senza interruzioni:
- `system_volume` viene automaticamente convertito in `set_volume`.
- `dark_mode` viene convertito in `quick_settings` con `setting: "dark_style"`.
- `app_launcher` viene convertito in `launch_application`.
- `system_media` viene convertito in `media_control`.

---

## 📦 Installazione e Gestione Dipendenze (`cargo` e `gnome-mcp-server`)

### Rilevamento e Installazione di `cargo`
All'avvio, il demone verifica la presenza di `cargo` (o `gnome-mcp-server` già installato). Se non presente, notifica il sistema e la GUI presenta una finestra di dialogo di consenso all'utente:
- Su Fedora/RHEL: `dnf5 install -y cargo` o via PackageKit D-Bus
- Su Ubuntu/Debian: `apt install -y cargo` o via PackageKit D-Bus
- Su Arch Linux: `pacman -S --noconfirm rust` o via PackageKit D-Bus
- Su openSUSE: `zypper install -y cargo` o via PackageKit D-Bus

### Compilazione di `gnome-mcp-server`
Una volta disponibile `cargo`, `gnome-mcp-server` può essere installato con un solo comando o tramite il gestore interno:
```bash
cargo install --git https://github.com/bilelmoussaoui/gnome-mcp-server
```
L'eseguibile viene posizionato in `~/.cargo/bin/gnome-mcp-server`, directory che viene automaticamente inclusa nel `PATH` dal runtime MCP.

---

## 🌐 Configurazione Server MCP (`mcp_servers.json`)

La configurazione risiede in `~/.config/voice-assistant/mcp_servers.json`:

```json
{
  "mcpServers": {
    "gnome-mcp-server": {
      "command": "gnome-mcp-server",
      "args": [],
      "env": {},
      "enabled": true,
      "description": "Integrazione nativa GNOME Desktop via gnome-mcp-server"
    }
  }
}
```

È possibile aggiungere ulteriori server MCP (es. filesystem, GitHub, memorie RAG esterne) tramite il Marketplace Smithery integrato nella finestra delle impostazioni dell'assistente.
