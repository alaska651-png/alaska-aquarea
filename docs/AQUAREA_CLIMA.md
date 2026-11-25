# Aquarea Clima: componenti e installazione

La dashboard principale è il sinottico Aquarea Clima. La card HACS è il suo contenitore HA; disegno, viste e controlli vengono serviti dal backend autenticato.

## Struttura completa

| Percorso | Funzione |
| --- | --- |
| aquarea-clima/web/index.html | Intestazione, firma Alaska e navigazione |
| aquarea-clima/web/app.js | Sezioni, controlli PDC, energia, storico, date e stanze |
| aquarea-clima/web/diagram.js | Sinottico SVG, circuiti, ACS e zone |
| aquarea-clima/web/cop.js | COP, cicli, modulazione ed esportazioni |
| aquarea-clima/web/styles.css | Layout desktop e mobile |
| aquarea-clima/web/reuse/ | Grafici, trasformazioni e statistiche HA |
| aquarea-clima/web/catalog.json | Catalogo; associazioni alle entità da completare localmente |
| aquarea-clima/server.py | Proxy autenticato, anteprima e conferma controlli |
| companion/common-backend/ | Collettore passivo HeishaMon/HA/MQTT |
| companion/cop-analyzer/ | Registrazione e analisi COP/cicli |
| companion/digital-twin/live/ | Piano, conferma e riscontro comandi PDC |
| companion/custom_components/aquarea_clima/ | Gateway sessione Home Assistant |
| coordinator/ | Regolazione stanze, Media e timer manuali |

## Installazione esistente

Percorso tipico: `/aquarea-clima/impianto`, card `custom:aquarea-clima-host`. La card chiede un accesso temporaneo al gateway HA e carica il sinottico nello stesso contesto autenticato. Il Coordinatore è su `/coordinatore-climatico/casa`.

Conservare la configurazione privata e un backup prima degli aggiornamenti. Installare i servizi compatibili prima di sostituire il frontend. Non copiare i modelli pubblici sopra i file privati e non trasferire approvazioni idrauliche da un altro impianto.

## Nuova installazione

Occorre predisporre separatamente:

1. HeishaMon, MQTT e integrazione HA con le entità locali.
2. Collettore comune e COP recorder, accessi privati e archiviazione.
3. Digital Twin con inventario, dizionario metriche, topologia e fatti di installazione propri, e variabile privata `HEISHAMON_EXPECTED_MAC` impostata all’identità verificata della propria ETH01. Senza identità configurata i comandi restano bloccati. I suoi file privati non sono approvazioni riutilizzabili della macchina di origine.
4. Backend `aquarea-clima/server.py`: `HA_URL`, `COMMON_URL`, `COMMON_CONFIG`, `API_TOKEN`, `LOCAL_UI_PASSWORD`, `LAN_HOST`, `WEB_PORT`, chiave privata `private/ha-bridge.key`. Dipendenze: aiohttp, paho-mqtt per i servizi MQTT e dati timezone.
5. Gateway HA con la medesima chiave di collegamento. Il file `aquarea-clima-sso/bridge.json` deve essere predisposto privatamente sul server HA, mai nel repository.
6. Card HACS `custom:aquarea-clima-host` in una vista panel.
7. Coordinatore e mappatura locale per la regolazione stanze, se usata.

Gateway e backend devono concordare su origine e porte. I riferimenti di rete della copia pubblica sono generici/loopback. Non esporre le porte private e non rimuovere i controlli di origine o autorizzazione.

I campi `entity` del catalogo sono intenzionalmente vuoti: associarli alle entità locali per lo storico dei parametri. Nessun dato dimostrativo sostituisce uno storico mancante.

## Cosa fa HACS

Scarica il frontend. Non installa il gateway Python, non avvia Docker, non procura token, non configura sensori e non abilita caldo/freddo. Repository personalizzato non significa inclusione nel catalogo predefinito HACS.
