"""Secret Service storage for MCP environment variables."""

import logging
from typing import Dict, Any

logger = logging.getLogger("VoiceAssistant.MCPCredentials")
SERVICE_NAME = "org.gnome.VoiceAssistant.MCP"


class MCPCredentialStore:
    """Stores MCP environment values in the desktop keyring, not JSON config."""

    @staticmethod
    def _credential_name(server_name: str, variable_name: str) -> str:
        return f"{server_name}:{variable_name}"

    def store_environment(self, server_name: str, environment: Dict[str, str]) -> Dict[str, Dict[str, str]]:
        try:
            import keyring
        except ImportError as error:
            raise RuntimeError("Il supporto keyring non e disponibile") from error

        references = {}
        for variable_name, value in environment.items():
            credential_name = self._credential_name(server_name, variable_name)
            keyring.set_password(SERVICE_NAME, credential_name, str(value))
            references[variable_name] = {"keyring": credential_name}
        return references

    def resolve_environment(self, environment: Dict[str, Any]) -> Dict[str, str]:
        try:
            import keyring
        except ImportError as error:
            raise RuntimeError("Il supporto keyring non e disponibile") from error

        resolved = {}
        for variable_name, value in environment.items():
            if isinstance(value, dict) and "keyring" in value:
                secret = keyring.get_password(SERVICE_NAME, value["keyring"])
                if secret is None:
                    raise RuntimeError(f"Credenziale mancante per {variable_name}")
                resolved[variable_name] = secret
            else:
                resolved[variable_name] = str(value)
        return resolved

    def delete_environment(self, server_name: str, environment: Dict[str, Any]) -> None:
        try:
            import keyring
        except ImportError:
            return

        for variable_name, value in environment.items():
            credential_name = value.get("keyring") if isinstance(value, dict) else self._credential_name(server_name, variable_name)
            try:
                keyring.delete_password(SERVICE_NAME, credential_name)
            except keyring.errors.PasswordDeleteError:
                continue
            except Exception:
                logger.warning("Impossibile eliminare la credenziale MCP %s", variable_name)
