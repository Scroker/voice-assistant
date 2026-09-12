from sentence_transformers import SentenceTransformer
import numpy as np

# 1. Carica il modello di embedding (verrà scaricato in automatico al primo avvio)
print("Caricamento del modello...")
model = SentenceTransformer('all-MiniLM-L6-v2')

# 2. Definisci i tuoi intenti e i trigger (il tuo "database" locale)
mcp_intents = {
    "quick_settings:wifi_on": [
        "accendi il wifi", 
        "connettiti a internet", 
        "attiva la rete senza fili"
    ],
    "media_control:pause": [
        "metti in pausa", 
        "ferma la musica", 
        "stoppa la riproduzione"
    ],
    "media_control:volume_up": [
        "alza il volume", 
        "metti più forte", 
        "non sento nulla"
    ]
}

# 3. Pre-calcola i vettori per tutti i trigger all'avvio
print("Vettorizzazione dei trigger in corso...")
intent_vectors = {}
for intent_name, examples in mcp_intents.items():
    # model.encode trasforma la lista di frasi in una matrice di vettori
    intent_vectors[intent_name] = model.encode(examples)

# Funzione matematica per calcolare la vicinanza (Cosine Similarity)
def cosine_similarity(v1, v2):
    return np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2))

# 4. Il motore vero e proprio
def route_user_query(user_query, threshold=0.80):
    """
    Trasforma la query dell'utente in un vettore, lo confronta con tutti
    i trigger salvati e restituisce l'intento se supera la soglia.
    """
    # Vettorizza la frase dell'utente
    query_vector = model.encode(user_query)
    
    best_intent = None
    best_score = -1.0

    # Confronta con ogni categoria
    for intent_name, vectors in intent_vectors.items():
        for trigger_vector in vectors:
            # Calcola la somiglianza (va da -1.0 a 1.0)
            score = cosine_similarity(query_vector, trigger_vector)
            
            if score > best_score:
                best_score = score
                best_intent = intent_name

    # Se il punteggio migliore supera la soglia di sicurezza, restituisci l'azione
    if best_score >= threshold:
        return best_intent, best_score
    else:
        return "UNKNOWN_INTENT", best_score

# ==========================================
# 5. TESTIAMO IL MOTORE
# ==========================================
if __name__ == "__main__":
    while True:
        test_query = input("\nCosa vuoi chiedere al sistema? (o 'exit' per uscire): ")
        if test_query.lower() == 'exit':
            break
            
        intent, score = route_user_query(test_query)
        
        print(f"--> Intento rilevato: {intent}")
        print(f"--> Affidabilità: {score:.2f}")
        
        if intent != "UNKNOWN_INTENT":
            print(f"[Sistema]: Eseguo la chiamata MCP per '{intent}' e passo i risultati al LLM.")
        else:
            print("[Sistema]: Nessun tool necessario, passo la domanda direttamente al LLM per una risposta generica.")