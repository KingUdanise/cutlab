# CutLab — taglio multicam sincronizzato per DaVinci Resolve

Il problema che risolve: quando una ripresa arriva su **due tracce video** (due
angolazioni della stessa scena) **più una traccia audio** (il lav
dell'interlocutore), i tagli automatici in giro tendono a lavorare su *una clip
selezionata*. Risultato: una traccia video sparisce e l'altra viene tagliata da
sola, l'audio si sfasa, il multicam diventa inutilizzabile.

CutLab non taglia: **ricostruisce** una timeline nuova appendendo lo stesso
sotto-range sorgente su ogni traccia, allo stesso frame. Pause via, cameraman
via, e le tracce restano allineate per costruzione. Non distrugge niente: la
timeline originale resta com'è.

## Installazione

1. Doppio click su **`dist\CutLabSetup.exe`** (installer Inno Setup, ~2 MB).
2. Nessun permesso di amministratore: installa in `%LOCALAPPDATA%\Programs\CutLab`.
3. Crea l'icona **CutLab** sul Desktop e nella Start Menu.

Requisiti: **Python 3.10+** con le dipendenze di `requirements.txt`
(`pip install -r requirements.txt`). L'installer cerca da solo il Python che le ha.

### Sviluppo / avvio senza installare

```powershell
python  C:\aivinci\cutlab\app.pyw      # con console, per vedere gli errori
pythonw C:\aivinci\cutlab\app.pyw      # finestra nativa
python  C:\aivinci\cutlab\server.py    # solo backend -> http://127.0.0.1:8778
```

### Ricompilare l'installer

```powershell
powershell -ExecutionPolicy Bypass -File build_installer.ps1   # richiede Inno Setup 6
```

## Flusso di lavoro

1. In Resolve apri la timeline grezza (2 camere + lav). Se vuoi limitare il
   lavoro a un pezzo, metti **In/Out**.
2. CutLab elenca le tracce con la percentuale di copertura del range: scegli la
   **traccia audio da analizzare** e le **tracce video da tenere sincronizzate**.
3. **Analizza**: VAD Silero per il parlato, embedding di voce per separare le
   voci, whisper per leggere le battute.
4. Rivedi: ogni battuta ha un colore (voce) e si accende/spegne con un click.
   La voce del cameraman si scarta con un click sul suo nome. `Shift+click`
   cambia la voce assegnata a una battuta (le correzioni a mano restano).
5. Regola le pause da togliere e i margini; ascolta l'**anteprima** del taglio.
6. **Crea timeline in Resolve**: nasce `<timeline> · CUT` con V1, V2 e A1
   tagliate agli stessi frame, un `.drp` di sicurezza esportato prima di scrivere,
   e una verifica in lettura che confronta posizione e durata di ogni pezzo.

## Come è fatto

| File | Ruolo |
|---|---|
| `resolve_api.py` | connessione, inventario multi-traccia, append resiliente |
| `analyze.py` | segnale allineato alla timeline, VAD, diarizzazione, ASR |
| `cutplan.py` | pause, margini, fusione, validazione per traccia |
| `build.py` | costruzione timeline sincronizzata + verifica |
| `server.py` | API FastAPI + anteprima audio |
| `web/` | UI (Manrope locale, nessun CDN) |

### Regole imparate a spese di Resolve 21.1 (non cambiarle)

* **`scriptapp("Resolve")` apre una connessione.** Ripeterlo a ogni chiamata
  esaurisce le connessioni e da lì in poi `AppendToTimeline` restituisce `None`
  senza errore. L'handle va cachato: era la causa di metà dei "fallimenti a caso".
* `AppendToTimeline` **non solleva** eccezioni: se l'handle del media è scaduto
  torna `None`. Serve una scala di tentativi (`ImportMedia` sul path è l'unico
  modo affidabile di ottenere un handle vivo, e non duplica il clip).
* `endFrame` è **esclusivo**: durata = `endFrame - startFrame`.
* `mediaType: 1` **non** trascina l'audio della clip video: A1 resta pulita.
* **`recordFrame` esplicito**: senza, l'arrotondamento a campione dell'audio
  sposta i clip di 1 frame e la deriva si accumula sui pezzi successivi.
* `GetEndFrame()` su una timeline appena creata restituisce 0: il range vero è
  quello dei clip, e va atteso che la timeline si assesti.
* `Project.DeleteTimeline` non esiste; `MediaPool.DeleteTimelines` vuole oggetti
  Timeline e risponde `False` comunque. Le timeline sono clip del media pool:
  si eliminano con `DeleteClips` nella cartella che le contiene.
* `SetCurrentTimeline` è **asincrona** e risponde `False` anche quando funziona:
  va verificata rileggendo il nome.
* Il VAD a soglia dBFS (quello che usano molti tool) si rompe sul rumore di
  fondo: Silero lavora sulla probabilità di voce, non sull'energia.

### Limiti noti

* La separazione delle voci è automatica ma **non infallibile** (sul materiale
  di prova: 8/10 battute corrette). Per questo la UI è fatta per correggere in
  due click: è più veloce che fidarsi e poi rifare.
* Se una traccia non copre un pezzo (clip più corta), quei frame vengono
  scartati e l'UI lo dice, invece di produrre una timeline sfalsata.
* I generatori/transizioni non hanno un media sorgente: vengono saltati e
  segnalati.

## Stato / roadmap

- [x] Inventario multi-traccia + mapping frame timeline → sorgente per traccia
- [x] VAD Silero + diarizzazione (ECAPA + MFCC + livello) con review
- [x] Piano con pause/margini/fusione e validazione per traccia
- [x] Build sincronizzata con `recordFrame` + verifica in lettura
- [x] Anteprima audio del risultato prima di toccare Resolve
- [ ] Correzione dei confini al frame dalla UI
- [ ] Salvataggio/caricamento dei preset di taglio
- [ ] Rilevamento automatico delle clip multicam non allineate (sync fra camere)
