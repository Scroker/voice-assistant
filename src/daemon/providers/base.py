import abc

class STTProvider(abc.ABC):
    @abc.abstractmethod
    def __init__(self, model: str, hardware: str, extra: dict):
        pass

    @abc.abstractmethod
    def process_chunk(self, data: bytes) -> tuple[str, str]:
        """
        Processa un chunk di audio.
        Ritorna una tupla: (text, partial_text)
        - text: il testo finale se è finita una frase, altrimenti stringa vuota.
        - partial_text: il testo parziale mentre l'utente sta parlando.
        """
        pass

    @abc.abstractmethod
    def flush_and_transcribe(self) -> str:
        """Forza la trascrizione dell'audio accumulato se il provider lavora in batch."""
        return ""

    @abc.abstractmethod
    def reset(self):
        """Resetta lo stato interno del riconoscitore."""
        pass

    @classmethod
    @abc.abstractmethod
    def get_available_models(cls) -> list[dict]:
        """Ritorna la lista dei modelli disponibili per questo provider."""
        pass

