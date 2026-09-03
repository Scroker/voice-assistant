# MCP Marketplace & Registry - Implementazione Completa

## Architettura Implementata

### Backend (Python - `src/daemon/mcp/`)

#### 1. **installer.py** - Ciclo di Vita Completo
```python
MCPServerInstaller
├── install_server(name, server_def, env_vars)
│   ├── Verifica disponibilità comando (uvx, npx, python, etc)
│   ├── Test di startup con --help
│   ├── Aggiunge server a mcp_servers.json
│   └── Ritorna (success, message)
├── uninstall_server(name)
│   ├── Rimuove da config
│   └── Protegge server built-in
├── test_server(name)
│   └── Verifica connessione funzionante
└── update_server_env(name, env_vars)
    └── Aggiorna variabili ambiente
```

#### 2. **registry.py** - Marketplace Discovery Potenziato
```python
MCPRegistryClient
├── get_featured() → Lista server con stato "installed"
├── search(query) → Ricerca remota + fallback locale
├── get_server_details(name) → Info dettagliate server
├── get_categories() → Lista categorie disponibili
└── filter_by_category(category) → Server per categoria

Features:
- Fetch da https://registry.smithery.ai
- Fallback a FEATURED_SERVERS offline
- Stato "installed" calcolato da mcp_servers.json
- Timeout 5s su query remote
```

#### 3. **manager.py** - D-Bus Methods Esposti
```python
MCPManager (potenziato con installer + registry)
├── Marketplace Discovery:
│   ├── get_marketplace_featured() → JSON
│   ├── search_marketplace(query) → JSON
│   ├── get_server_details(name) → JSON
│   ├── get_marketplace_categories() → JSON
│   └── filter_marketplace_by_category(cat) → JSON
├── Installation:
│   ├── install_mcp_server(name, config, env_vars) → (bool, msg)
│   ├── uninstall_mcp_server(name) → (bool, msg)
│   ├── test_mcp_server(name) → (bool, msg)
│   └── update_server_config(name, env_vars, enabled) → (bool, msg)
└── Status:
    └── get_installed_servers() → JSON
```

### Frontend (JavaScript - `src/prefs/mcp.js`)

#### UI Structure: Stack di 3 Tab

**Tab 1: Marketplace**
```
┌─ Search Bar (ricerca in tempo reale)
├─ Server Grid (2 colonne, card-based)
│  ├─ Titolo, descrizione, categoria
│  ├─ Button "Installa" / Badge "✓ Installato"
│  └─ Click → Install Dialog
└─ Install Dialog
   ├─ Anteprima comando
   ├─ Form env vars (se richieste)
   └─ Button Installa (async call)
```

**Tab 2: Server Installati**
```
┌─ ListBox con azioni:
│  ├─ Toggle Enable/Disable
│  ├─ Button Config (edit env vars)
│  └─ Button Uninstall
└─ Empty state se nessuno installato
```

**Tab 3: Configurazione Avanzata**
```
├─ Toggle MCP (enable/disable integrazione)
├─ URL Registry (default: https://registry.smithery.ai)
└─ Info panel (documentazione)
```

### D-Bus Integration

```xml
<!-- Interface esposta al daemon -->
<interface name="org.gnome.shell.extensions.voice_assistant.MCP">
  <!-- Marketplace Methods -->
  <method name="GetMarketplaceFeatured">
    <arg type="s" direction="out" name="servers_json"/>
  </method>
  <method name="SearchMarketplace">
    <arg type="s" direction="in" name="query"/>
    <arg type="s" direction="out" name="results_json"/>
  </method>
  <method name="GetServerDetails">
    <arg type="s" direction="in" name="server_name"/>
    <arg type="s" direction="out" name="details_json"/>
  </method>
  
  <!-- Installation Methods -->
  <method name="InstallMCPServer">
    <arg type="s" direction="in" name="name"/>
    <arg type="s" direction="in" name="config_json"/>
    <arg type="s" direction="in" name="env_vars_json"/>
    <arg type="b" direction="out" name="success"/>
    <arg type="s" direction="out" name="message"/>
  </method>
  <method name="UninstallMCPServer">
    <arg type="s" direction="in" name="name"/>
    <arg type="b" direction="out" name="success"/>
    <arg type="s" direction="out" name="message"/>
  </method>
  <method name="TestMCPServer">
    <arg type="s" direction="in" name="name"/>
    <arg type="b" direction="out" name="success"/>
    <arg type="s" direction="out" name="message"/>
  </method>
  
  <!-- Configuration Methods -->
  <method name="UpdateServerConfig">
    <arg type="s" direction="in" name="name"/>
    <arg type="s" direction="in" name="env_vars_json"/>
    <arg type="b" direction="in" name="enabled"/>
    <arg type="b" direction="out" name="success"/>
    <arg type="s" direction="out" name="message"/>
  </method>
  <method name="GetInstalledServers">
    <arg type="s" direction="out" name="servers_json"/>
  </method>
</interface>
```

## Flusso di Installazione Utente

1. **Utente apre Preferences → Tools (MCP)**
2. **Browsing**: 
   - Vede server featured in grid
   - Ricerca "database" → filtra risultati in real-time
   - Clicca su "SQLite Query Tool" → vede dettagli
3. **Install**:
   - Clicca "Installa"
   - Dialog con anteprima comando: `uvx mcp-server-sqlite --db-path ~/.local/share/voice-assistant/database.db`
   - Se richieste env vars (es: `OPENAI_API_KEY`), form per inserirle
   - Clicca "Installa" → backend:
     - Verifica `uvx --version` disponibile
     - Testa `uvx mcp-server-sqlite --help`
     - Aggiunge a `~/.config/voice-assistant/mcp_servers.json`
     - Ritorna "(success, message)"
4. **Management**:
   - Vede server in "Server Installati" tab
   - Toggle Enable → attiva il server al prossimo restart
   - Clicca Config → edita env vars
   - Clicca Uninstall → rimuove da config

## Configurazione (`~/.config/voice-assistant/mcp_servers.json`)

```json
{
  "mcpServers": {
    "gnome-system": {
      "command": "builtin",
      "args": [],
      "env": {},
      "enabled": true,
      "description": "Native GNOME desktop controls"
    },
    "sqlite-db": {
      "command": "uvx",
      "args": ["mcp-server-sqlite", "--db-path", "~/.local/share/voice-assistant/database.db"],
      "env": {},
      "enabled": false,
      "description": "Query local SQLite databases",
      "installed_at": 1725278342.123
    }
  }
}
```

## Dipendenze Backend

Aggiungi a `src/daemon/mcp/`:
- `urllib` (built-in) - fetch registry remota
- `asyncio` (built-in) - async operations
- `subprocess` (built-in) - verificare comandi disponibili
- `json` (built-in) - parsing config

**Nessuna dipendenza esterna aggiunta** ✅

## Dipendenze Frontend

Già disponibili in GNOME 46+:
- `Gtk` 4.12+
- `Adw` 1.5+ (Libadwaita)
- `Gio` (D-Bus)

## File Modificati / Creati

```
src/daemon/mcp/
├── installer.py              [NUOVO] 160 righe
├── registry.py               [MODIFICATO] +80 righe (metodi marketplace)
├── manager.py                [MODIFICATO] +110 righe (D-Bus methods)
├── config.py                 [INVARIATO] ✓
└── client.py                 [INVARIATO] ✓

src/prefs/
└── mcp.js                    [MODIFICATO] 280 → 450 righe (UI completa)
```

## Test Verificati

### Backend
```python
# test_installer.py
await installer.install_server('test-server', {...}, {})
await installer.test_server('test-server')
await installer.update_server_env('test-server', {'API_KEY': 'test'})
await installer.uninstall_server('test-server')

# test_registry.py
await registry.get_featured()  # 4 server featured
await registry.search('database')  # filtra
await registry.search('web')  # fallback a featured
await registry.get_categories()  # ['Desktop', 'Web', 'Productivity', 'Data']
```

### Frontend (manual)
- ✅ Load marketplace featured in grid
- ✅ Search real-time filtra risultati
- ✅ Install dialog con env var form
- ✅ Installed tab mostra server con toggle
- ✅ Config button clickabile (placeholder)
- ✅ Uninstall rimuove da list

## Roadmap Futura

- [ ] **Health Status**: WebSocket polling per verificare "server ok" / "server down"
- [ ] **Version Management**: Traccia versione installata, proponi upgrade
- [ ] **Auto-start Toggle**: Per server che non devono attivarsi subito
- [ ] **Advanced Config UI**: Dialog per edit env vars post-installation
- [ ] **Categories Grid**: Browsing per categoria (Desktop, Web, Data, etc)
- [ ] **Reviews/Ratings**: Integrazione feedback da registry
- [ ] **Dependency Resolution**: Installa automaticamente `uvx`, `npx`, etc
- [ ] **CLI Tool**: `voice-assistant-mcp install sqlite-db` da terminal

## Note di Implementazione

1. **Isolamento D-Bus**: MCPManager espone metodi che ritornano JSON stringhe (non object), safer per D-Bus
2. **Fallback Offline**: Se registry.smithery.ai non raggiungibile, usa FEATURED_SERVERS hardcoded
3. **Built-in Protection**: Server "gnome-system" non può essere disinstallato
4. **Timeouts**: 5s per fetch remote, 10s per test server startup
5. **Error Handling**: Tutti i metodi ritornano (success: bool, message: str) strutturato
6. **Env Var Security**: Password/API keys memorizzate plaintext in config JSON (TODO: encryption)
