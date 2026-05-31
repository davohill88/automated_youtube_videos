# automated_youtube_videos

Automated YouTube video creation pipeline for upcoming Fantagraphics releases.

## Setup

Create and activate a virtual environment:

```sh
python3 -m venv venv
source venv/bin/activate
```

Install dependencies:

```sh
pip install -r requirements.txt
```

Create a local environment file:

```sh
cp .env.example .env
```

Then edit `.env` and add your Anthropic API key:

```sh
ANTHROPIC_API_KEY=your_api_key_here
```

## Usage

Scrape upcoming Fantagraphics products:

```sh
python fantagraphics_scraper.py
```

Optionally enrich scraped products with creator, ISBN, and release date data from comicreleases.com:

```sh
python fantagraphics_scraper.py --enrich
```

Generate a YouTube episode script:

```sh
python script_generator.py
```

Choose a style preset:

```sh
python script_generator.py --style hype
python script_generator.py --style chill
python script_generator.py --style enthusiast
```

Limit the number of books included while testing:

```sh
python script_generator.py --max-books 3
```

## Outputs

The scraper writes product data to:

```text
data/fantagraphics_upcoming.json
```

The script generator writes:

```text
scripts/*.json
scripts/*.tts.txt
scripts/*.timeline.json
```

These output folders are ignored by git because they contain generated data.
