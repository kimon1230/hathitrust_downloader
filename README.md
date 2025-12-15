# HathiTrust Downloader

Download books from HathiTrust Digital Library as PDF files.

**MAKE SURE YOU ARE COMPLYING WITH COPYRIGHT**

## Features

- Download individual pages and merge into a single PDF
- Batch download multiple books via YAML configuration
- Resume interrupted downloads
- Rate limiting with automatic retry and exponential backoff
- Browser impersonation to avoid blocks (via curl_cffi)
- Graceful shutdown with CTRL+C

## Installation

```bash
git clone https://github.com/yourusername/hathitrust_downloader.git
cd hathitrust_downloader
pip install -r requirements.txt
```

For better success rates, install the optional `curl_cffi` package:

```bash
pip install curl_cffi
```

## Usage

### Single Book

```bash
# Download entire book
python hathitrust_download.py -l "uc1.$c148966" -o "my_book.pdf"

# Download specific page range
python hathitrust_download.py -l "uc1.$c148966" -b 1 -e 50 -o "partial.pdf"

# Use safe mode (longer delays) if getting blocked
python hathitrust_download.py -l "uc1.$c148966" -o "my_book.pdf" --safe
```

### Batch Download

Create a YAML file (see `books_sample.yaml`):

```yaml
books:
  - id: 'uc1.$c146800'
    start: 1
    end: 354
    output: "Volume_32.pdf"

  - id: 'uc1.$c148966'
    start: 1
    end: 240
    output: "Volume_33.pdf"
```

Then run:

```bash
python hathitrust_download.py -f books.yaml
```

### Options

| Option | Description |
|--------|-------------|
| `-l, --link` | HathiTrust URL or book ID |
| `-f, --file` | YAML file for batch processing |
| `-o, --output` | Output PDF filename |
| `-b, --begin` | First page to download (default: 1) |
| `-e, --end` | Last page to download (0 = all) |
| `-p, --pages` | Total page count if known (skips detection) |
| `-k, --keep` | Keep individual page PDFs after merging |
| `-d, --delay` | Minimum delay between requests (default: 5s) |
| `--safe` | Safe mode with longer delays (10-20s) |
| `-v, --verbose` | Verbose output |

## Requirements

- Python 3.8+
- requests
- pypdf (or PyPDF2)
- pyyaml (for batch mode)
- curl_cffi

## Notes

- You are responsible for copyright or any other legal compliance
- Be respectful of HathiTrust's servers - use reasonable delays
- Some books may have access restrictions
- Downloads can be interrupted with CTRL+C and resumed later

## License

MIT License - see [LICENSE](LICENSE) for details.
