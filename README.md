# Music Downloader MP3

[![Tests](https://github.com/Weysmar/music-downloader-mp3/actions/workflows/tests.yml/badge.svg)](https://github.com/Weysmar/music-downloader-mp3/actions/workflows/tests.yml)

Télécharge des **discographies complètes** et des bibliothèques Spotify en **MP3 entièrement taggués**, depuis YouTube Music, **sans compte Google ni cookies**.

> **English** — Downloads full artist discographies (from MusicBrainz) or Spotify libraries as fully tagged MP3 files (Picard-grade tags, 1200px cover art, synced lyrics), using YouTube Music as the audio source. No Google account, no cookies. Windows, macOS and Linux. Commands are shown below; run `python main.py --help` for all options.

```bash
python main.py artist "Daft Punk"
```

```text
Daft Punk/
├── 1997 - Homework/
│   ├── 01 - Daftendirekt.mp3
│   ├── 02 - WDPK 83.7 FM.mp3
│   ├── ...
│   └── cover.jpg
└── 2001 - Discovery/
    └── ...
```

## Ce que fait chaque étape

1. **Liste des morceaux**
   - **MusicBrainz** (recommandé, sans clé API) : albums, EP et singles d'un artiste. Pour chaque album, une seule édition est retenue (l'originale, ou la plus complète avec `--edition largest`). Les lives, compilations et remixes sont exclus par défaut (option `--include`), ainsi que les doublons single/album et les morceaux d'autres artistes présents sur les singles partagés.
   - **Spotify** (optionnel) : titres likés, playlists dont tu es propriétaire, discographie d'un artiste.
2. **Recherche audio** : recherche dans YouTube Music d'abord par ISRC, puis dans les *songs* (audio officiel, sans intro de clip), puis dans les vidéos. Chaque résultat reçoit un score selon le titre, l'artiste et la **durée** :
   - les versions live, cover, remix, sped up… sont écartées si le morceau d'origine n'en est pas une ;
   - un autre morceau du même artiste, ou le même morceau joué par un autre artiste, est toujours refusé. Mieux vaut un morceau manquant qu'un mauvais morceau.
3. **Conversion** : la meilleure piste audio disponible (Opus ~130-160 kbps) est convertie en **MP3 VBR V0** (~245 kbps). Ce réglage conserve tout ce que contient la source, sans gonfler inutilement les fichiers.
4. **Tags** : les mêmes que MusicBrainz Picard.
   - Titre, artiste(s), artiste de l'album, album, n° de piste et de disque, année originale et date de l'édition, genres, label, n° de catalogue, code-barres, ISRC, identifiants MusicBrainz.
   - **Pochette 1200×1200**, **paroles** (LRCLIB) intégrées au MP3, et `.lrc` synchronisé à côté.
5. **Rangement** : `<dossier de téléchargement>/<Artiste>/<Année> - <Album>/<NN> - <Titre>.mp3`, avec `cover.jpg` par album.

## Installation

### 1. Prérequis

| | Windows | macOS | Linux (Debian/Ubuntu) |
| --- | --- | --- | --- |
| **Python 3.10+** | [python.org](https://www.python.org/downloads/) (cocher *Add to PATH*) ou `winget install Python.Python.3.12` | `brew install python` | `sudo apt install python3 python3-venv` |
| **FFmpeg** (ffmpeg + ffprobe) | `winget install Gyan.FFmpeg` | `brew install ffmpeg` | `sudo apt install ffmpeg` |
| **Node.js** ou **Deno** (utilisé par yt-dlp pour YouTube) | `winget install OpenJS.NodeJS.LTS` | `brew install node` | `sudo apt install nodejs` |

Sous Windows, tu peux aussi poser `ffmpeg.exe` et `ffprobe.exe` directement dans le dossier du projet au lieu de les installer.

Après une installation avec `winget`, ferme et rouvre le terminal pour que les commandes soient trouvées.

### 2. Le projet

```bash
git clone https://github.com/Weysmar/music-downloader-mp3.git
cd music-downloader-mp3
python -m venv .venv
```

Active l'environnement virtuel :

- Windows (PowerShell) : `.venv\Scripts\Activate.ps1`
- macOS / Linux : `source .venv/bin/activate`

Puis installe les dépendances et crée ta configuration :

```bash
pip install -r requirements.txt
cp .env.example .env          # Windows : copy .env.example .env
```

Le fichier `.env` est facultatif pour la commande `artist`. Il sert à changer le dossier de téléchargement et à renseigner les clés Spotify.

Vérifie que tout fonctionne :

```bash
python -m pytest tests
```

## Utilisation

```bash
# Discographie complète (MusicBrainz, sans clé)
python main.py artist "Daft Punk"
python main.py artist "Iron Maiden" --include live          # avec les albums live
python main.py artist "Orelsan" --edition largest           # éditions deluxe / bonus tracks
python main.py artist --mbid 5b11f4ce-a62d-471e-81fc-a69a8278c7da   # homonymes : Nirvana (groupe US)
python main.py artist "Gazo" --extract-only                 # crée la liste sans télécharger

# Spotify (clés dans .env)
python main.py spotify-library --enrich       # titres likés + tes playlists, complétés par MusicBrainz
python main.py spotify-artist "Angèle" --enrich

# Reprendre / relancer (un simple nom de fichier est cherché dans data/)
python main.py download "Daft Punk_mb_library.json"           # ignore les fichiers déjà présents
python main.py download "failed_Daft Punk_mb_library.json"    # ne relance que les échecs

# Métadonnées
python main.py enrich library.json    # ajoute les métadonnées MusicBrainz (par ISRC)
python main.py retag library.json     # réécrit les tags des fichiers déjà téléchargés

python main.py -v ...                 # détail des scores de correspondance
```

Un téléchargement interrompu (Ctrl+C, coupure) reprend là où il s'était arrêté : il suffit de relancer la même commande.

Options utiles :

- `--output-dir DIR` : dossier de destination pour ce lancement.
- `--workers N` : nombre de téléchargements en parallèle.
- `--no-lyrics` : ne pas chercher les paroles.
- `--types album,ep,single` : types de sorties à inclure.
- `--limit N` : nombre maximal de morceaux.
- `--keep-duplicates` : garder les doublons.

## Configuration (`.env`)

Tous les réglages sont facultatifs. Une valeur invalide est remplacée par la valeur par défaut, avec un avertissement.

| Réglage | Défaut | Rôle |
| --- | --- | --- |
| `DOWNLOAD_DIR` | `downloads` | Dossier des MP3, par exemple `D:\Musique` ou `~/Music`. |
| `DATA_DIR` | `data` | Listes de morceaux, échecs et journal. |
| `CONCURRENT_DOWNLOADS` | `3` | Téléchargements en parallèle. |
| `RATE_LIMIT_DELAY` | `2.0` | Pause (s) après chaque téléchargement. |
| `MP3_QUALITY` | `0` | `0` = VBR V0 (recommandé), ou un débit comme `320`. |
| `MATCH_THRESHOLD` | `0.72` | Score minimal (0-1) pour accepter un résultat YouTube Music. |
| `ID3_VERSION` | `3` | `3` = compatibilité maximale, `4` = valeurs multiples natives. |
| `COVER_SIZE` | `1200` | `250`, `500`, `1200` ou `full`. |
| `FETCH_LYRICS` / `SAVE_LRC` | `true` | Paroles dans le MP3 / fichier `.lrc` synchronisé. |
| `SAVE_FOLDER_COVER` | `true` | `cover.jpg` dans chaque dossier d'album. |
| `MUSICBRAINZ_CONTACT` | | E-mail ou URL de contact, demandé par MusicBrainz. |
| `PREFERRED_COUNTRIES` | `XW,FR,XE,US,GB` | Pays préférés pour choisir l'édition d'un album. |
| `SPOTIPY_CLIENT_ID` / `SECRET` | | Uniquement pour les commandes Spotify. |

### Clés Spotify (facultatif)

1. Crée une app sur le [tableau de bord Spotify](https://developer.spotify.com/dashboard). Depuis 2026, son propriétaire doit avoir Spotify Premium.
2. Ajoute `http://127.0.0.1:8080/callback` comme *Redirect URI*.
3. Copie le *Client ID* et le *Client Secret* dans `.env`.

Au premier lancement, une page de connexion Spotify s'ouvre dans le navigateur.

## Où sont les fichiers

- **MP3** : dans `DOWNLOAD_DIR`.
- **`data/`** : les listes de morceaux (`<Artiste>_mb_library.json`, `library.json`), les échecs (`failed_*.json`, avec la raison de chaque échec) et le journal `spotify_to_mp3.log`.

## Dépannage

| Symptôme | Solution |
| --- | --- |
| `FFmpeg not found` | Installe FFmpeg (voir prérequis), ou pose `ffmpeg` **et** `ffprobe` dans le dossier du projet. |
| Erreurs 403, « format not available », beaucoup d'échecs d'un coup | YouTube a changé : `pip install -U "yt-dlp[default]"`. |
| « confirm you're not a bot », pauses fréquentes | Trop de volume : baisse `CONCURRENT_DOWNLOADS` ou augmente `RATE_LIMIT_DELAY`. |
| Mauvais artiste (homonyme) | Le script liste les autres artistes du même nom : relance avec `--mbid <ID>`. |
| Morceaux manquants | Ils sont absents de YouTube Music ou n'y existent qu'en live/clip : voir `data/failed_*.json`. |

Pour vérifier en conditions réelles (MusicBrainz, YouTube Music, LRCLIB, un vrai téléchargement) :

```bash
RUN_LIVE_TESTS=1 python -m pytest tests/test_live.py -v
```

Sous PowerShell : `$env:RUN_LIVE_TESTS=1; python -m pytest tests/test_live.py -v`. Si ces tests échouent alors que les autres passent, c'est que YouTube a changé : mets yt-dlp à jour.

## Limites connues

- **Qualité** : sans compte, YouTube fournit au mieux de l'Opus ~130-160 kbps (l'AAC 256 kbps est réservé aux comptes Premium). Le MP3 V0 conserve tout ce que contient la source. Pour une vraie qualité « CD », il faut une source sans perte (CD, FLAC acheté sur Bandcamp ou Qobuz).
- **Blocages YouTube** : sans cookies, YouTube peut limiter les gros volumes. Le script fait alors une pause automatique, de plus en plus longue.
- **API Spotify (règles de février 2026)** : seules les playlists dont tu es propriétaire ou collaborateur sont lisibles, et le label n'est plus fourni, d'où l'intérêt de `--enrich`.

## Tests

```bash
python -m pytest tests     # ~120 tests hors ligne (réseau bloqué, YouTube simulé), quelques secondes
```

Ils tournent automatiquement sous Windows, macOS et Linux à chaque push (GitHub Actions).

## Avertissement

Ce projet est destiné à un usage personnel et éducatif. Télécharger du contenu protégé par le droit d'auteur peut être interdit dans ton pays, et l'usage de YouTube est soumis à ses [conditions d'utilisation](https://www.youtube.com/t/terms). Tu es seul responsable de l'usage que tu fais de cet outil. Soutiens les artistes : achète leur musique ou écoute-la sur les plateformes officielles.

## Licence

[MIT](LICENSE)
