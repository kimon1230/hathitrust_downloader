#!/usr/bin/env python3
"""
HathiTrust PDF downloader
Grabs pages and merges to PDF
"""

import argparse
import datetime
import os
import random
import re
import signal
import sys
import time
from email.utils import parsedate_to_datetime
from pathlib import Path

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

try:
    from curl_cffi import requests
    USE_CURL_CFFI = True
except ImportError:
    import requests
    USE_CURL_CFFI = False

shutdown_requested = False

def signal_handler(signum, frame):
    global shutdown_requested
    if shutdown_requested:
        print("\n\nForce quitting...")
        sys.exit(1)
    shutdown_requested = True
    print("\n\nShutdown requested. Finishing current downloads and merging completed pages...")
    print("Press CTRL+C again to force quit.")

signal.signal(signal.SIGINT, signal_handler)

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15',
]

def get_headers():
    return {
        'User-Agent': random.choice(USER_AGENTS),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'same-origin',
        'Sec-Fetch-User': '?1',
        'Cache-Control': 'max-age=0',
    }

session = None


def init_session():
    global session
    if USE_CURL_CFFI:
        session = requests.Session(impersonate='chrome')
    else:
        session = requests.Session()
        session.headers.update(get_headers())


def reset_session():
    init_session()
    try:
        session.get("https://babel.hathitrust.org/", timeout=30)
        time.sleep(random.uniform(1, 3))
    except Exception:
        pass  # not critical if this fails


def parse_retry_after(response):
    retry_after = response.headers.get('Retry-After')
    if not retry_after:
        return None

    retry_after = retry_after.strip()

    try:
        seconds = int(retry_after)
        return seconds if seconds >= 0 else None
    except ValueError:
        pass

    try:
        retry_datetime = parsedate_to_datetime(retry_after)
        now = datetime.datetime.now(datetime.timezone.utc)
        delta = retry_datetime - now
        return max(0, delta.total_seconds())
    except (ValueError, TypeError, OverflowError):
        return None


try:
    from pypdf import PdfWriter, PdfReader
except ImportError:
    try:
        from PyPDF2 import PdfMerger, PdfReader
        USE_PYPDF2 = True
    except ImportError:
        print("Error: Please install pypdf: pip install pypdf")
        sys.exit(1)
else:
    USE_PYPDF2 = False

def sanitize_filename(filename):
    # strip out chars that cause problems in filenames
    bad_chars = {'.': '_', '$': '_', ':': '_', '/': '_', '\\': '_', ' ': '_',
                 '?': '_', '*': '_', '"': '_', '<': '_', '>': '_', '|': '_'}
    for char, replacement in bad_chars.items():
        filename = filename.replace(char, replacement)
    return filename

def extract_book_id(url):
    # try to extract book ID from URL or just return it if it's already an ID
    match = re.search(r'id=([^&]+)', url)
    if match:
        return match.group(1)

    match = re.search(r'2027/([^?&]+)', url)
    if match:
        return match.group(1)

    # probably already a book ID if it has a dot and isnt a URL
    if '.' in url and not url.startswith(('http://', 'https://')):
        return url

    return None

def get_page_count(book_id):
    # try the API first
    api_url = f"https://babel.hathitrust.org/cgi/htd/structure/{book_id}"

    try:
        response = session.get(api_url, timeout=30)
        response.raise_for_status()
        data = response.json()

        # check STRUCT1 section
        if 'STRUCT1' in data:
            contents = data.get('STRUCT1', {}).get('contents', [])
            page_count = sum(1 for item in contents if isinstance(item, dict))
            if page_count > 0:
                return page_count

        # fallback: look for seq numbers
        if 'pg' in str(data):
            pages = re.findall(r'"seq":(\d+)', str(data))
            if pages:
                return max(int(p) for p in pages)

    except Exception as e:
        print(f"API method failed: {e}")
    
    # fallback to HTML parsing - less reliable but works sometimes
    try:
        html_url = f"https://babel.hathitrust.org/cgi/pt?id={book_id}"
        response = session.get(html_url, timeout=30)
        response.raise_for_status()

        # try a bunch of different patterns - hathitrust keeps changing their HTML
        patterns = [
            r'"total_seq"\s*:\s*(\d+)',
            r'"totalSeq"\s*:\s*(\d+)',
            r'"total"\s*:\s*(\d+)',
            r'data-total-seq="(\d+)"',
            r'data-seq="(\d+)"[^>]*>\s*(?:Last|End)',
            r'of\s+(\d+)\s+page',
            r'total["\s:]+(\d+)',
            r'/seq/(\d+)"?\s*>\s*(?:Last|End)',
            r'seq=(\d+)[^>]*>\s*\d+\s*</a>\s*$',
            r'"defaultSeq"\s*:\s*\d+\s*,\s*"totalSeq"\s*:\s*(\d+)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, response.text, re.IGNORECASE)
            if match:
                return int(match.group(1))
                
    except Exception as e:
        print(f"HTML parsing method failed: {e}")
    
    return None

def get_page_count_by_probing(book_id, start=100, max_pages=2000):
    # binary search to find the last valid page
    # kinda slow but works when API fails
    print("Probing for page count (this may take a moment)...")

    def page_exists(seq):
        url = f"https://babel.hathitrust.org/cgi/imgsrv/download/pdf?id={book_id}&seq={seq}"
        try:
            response = session.head(url, timeout=10, allow_redirects=True)
            return response.status_code == 200
        except:
            return False

    # find upper bound
    upper = start
    while upper <= max_pages and page_exists(upper):
        upper *= 2

    if upper > max_pages:
        upper = max_pages

    # binary search between lower and upper
    lower = upper // 2
    while lower < upper:
        mid = (lower + upper + 1) // 2
        if page_exists(mid):
            lower = mid
        else:
            upper = mid - 1

    return lower if page_exists(lower) else None

def is_valid_pdf(filepath):
    try:
        with open(filepath, 'rb') as f:
            header = f.read(8)
            if not header.startswith(b'%PDF'):
                return False
            # make sure it's not just a tiny error page
            f.seek(0, 2)
            return f.tell() >= 1000
    except (IOError, OSError, PermissionError):
        return False


# global state for rate limiting
_download_count = 0
_last_pause_time = time.time()
_consecutive_failures = 0
MAX_CONSECUTIVE_FAILURES = 6  # give up after 6 failures in a row


def reset_download_counter():
    global _download_count, _last_pause_time
    _download_count = 0
    _last_pause_time = time.time()


def download_page(book_id, seq, output_dir, delay_range=(5, 12), retries=3):
    global shutdown_requested, _download_count, _last_pause_time

    if shutdown_requested:
        return None, False, seq

    url = f"https://babel.hathitrust.org/cgi/imgsrv/download/pdf?id={book_id}&seq={seq}"
    output_path = os.path.join(output_dir, f"page_{seq:05d}.pdf")

    if os.path.exists(output_path) and is_valid_pdf(output_path):
        return output_path, True, seq

    _download_count += 1
    base_wait = random.uniform(delay_range[0], delay_range[1])

    # take random breaks to look more human
    if _download_count % random.randint(10, 20) == 0:
        extra_pause = random.uniform(5, 15)
        print(f"\n  Taking a break ({base_wait + extra_pause:.0f}s)...", end='', flush=True)
        time.sleep(base_wait + extra_pause)
    else:
        # add some randomness so we dont look like a bot
        jitter = random.gauss(0, 1.5)
        wait = max(3, base_wait + jitter)
        time.sleep(wait)

    for attempt in range(retries):
        if shutdown_requested:
            return None, False, seq
        try:
            headers = {
                'Referer': f'https://babel.hathitrust.org/cgi/pt?id={book_id}&seq={seq}',
            }
            response = session.get(url, timeout=60, headers=headers)

            if response.status_code in (429, 403):
                global _consecutive_failures
                _consecutive_failures += 1
                error_type = "Rate limited" if response.status_code == 429 else "Forbidden"

                if _consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    print(f"\n{error_type} on page {seq} - giving up after {_consecutive_failures} consecutive failures")
                    _consecutive_failures = 0
                    return None, False, seq

                server_wait = parse_retry_after(response)
                if server_wait is not None:
                    wait_time = server_wait * random.uniform(1.0, 1.1)
                    print(f"\n{error_type} on page {seq} - server requested {server_wait:.0f}s wait, waiting {wait_time:.0f}s...")
                else:
                    # exponential backoff
                    base_wait = 30
                    wait_time = min(base_wait * (2 ** (_consecutive_failures - 1)), 600) * random.uniform(0.8, 1.2)
                    print(f"\n{error_type} on page {seq} (failure {_consecutive_failures}/{MAX_CONSECUTIVE_FAILURES}), waiting {wait_time:.0f}s...")

                if _consecutive_failures >= 3:
                    print("Refreshing session...")
                    reset_session()

                time.sleep(wait_time)
                continue

            response.raise_for_status()

            # check if we got redirected to a different page
            actual_seq = seq
            if response.url:
                match = re.search(r'seq=(\d+)', response.url)
                if match:
                    actual_seq = int(match.group(1))

            content_type = response.headers.get('Content-Type', '')
            if 'pdf' not in content_type.lower() and not response.content.startswith(b'%PDF'):
                # sometimes we get HTML error pages instead of PDFs
                if attempt < retries - 1:
                    time.sleep(random.uniform(30, 60))
                    continue
                else:
                    print(f"\nPage {seq}: received non-PDF response")
                    # print(f"DEBUG: got content type {content_type}")  # uncomment for debugging
                    return None, False, actual_seq

            with open(output_path, 'wb') as f:
                f.write(response.content)

            if not is_valid_pdf(output_path):
                os.remove(output_path)
                if attempt < retries - 1:
                    time.sleep(random.uniform(30, 60))
                    continue
                else:
                    print(f"\nPage {seq}: invalid PDF received")
                    return None, False, actual_seq

            _consecutive_failures = 0
            return output_path, True, actual_seq

        except Exception as e:
            if attempt < retries - 1:
                time.sleep(random.uniform(30, 60))
            else:
                print(f"\nFailed to download page {seq}: {e}")
                return None, False, seq

    return None, False, seq

def retry_failed_pages(failed_pages, book_id, temp_dir, delay_range):
    global shutdown_requested

    downloaded = []
    still_failed = []

    print(f"\nRetrying {len(failed_pages)} pages...")
    for seq in failed_pages:
        if shutdown_requested:
            # add back everything we didn't get to
            still_failed.extend([s for s in failed_pages if s >= seq])
            break

        print(f"  Retrying page {seq}...", end='', flush=True)
        filepath, success, actual_seq = download_page(book_id, seq, temp_dir, delay_range, retries=3)
        if success:
            downloaded.append((actual_seq, filepath))
            print(" OK")
        else:
            still_failed.append(seq)
            print(" FAILED")

    return downloaded, still_failed

def merge_pdfs(pdf_files, output_path):
    if USE_PYPDF2:
        merger = PdfMerger()
        for pdf_file in pdf_files:
            try:
                merger.append(pdf_file)
            except Exception as e:
                print(f"Warning: Could not add {pdf_file}: {e}")
        merger.write(output_path)
        merger.close()
    else:
        writer = PdfWriter()
        for pdf_file in pdf_files:
            try:
                reader = PdfReader(pdf_file)
                for page in reader.pages:
                    writer.add_page(page)
            except Exception as e:
                print(f"Warning: Could not add {pdf_file}: {e}")

        with open(output_path, 'wb') as f:
            writer.write(f)

def load_books_yaml(filepath):
    if not HAS_YAML:
        print("Error: PyYAML is required for batch processing.")
        print("Install with: pip install pyyaml")
        sys.exit(1)

    try:
        with open(filepath, 'r') as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        print(f"Error: YAML file not found: {filepath}")
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"Error: Invalid YAML file: {e}")
        sys.exit(1)

    if not data or 'books' not in data:
        print("Error: YAML file must contain a 'books' list")
        sys.exit(1)

    books = data['books']
    if not books:
        print("Error: No books found in YAML file")
        sys.exit(1)

    validated_books = []
    required_fields = ['id', 'start', 'end', 'output']

    for i, book in enumerate(books, 1):
        if not isinstance(book, dict):
            print(f"Error: Book {i} is not a valid dictionary")
            sys.exit(1)

        # check for missing required fields
        missing = [field for field in required_fields if field not in book]
        if missing:
            print(f"Error: Book {i} missing required field(s): {', '.join(missing)}")
            sys.exit(1)

        book_id = str(book['id'])

        # warn about common mistake with $ in YAML
        if '.' in book_id and '$' not in book_id and 'uc1.' in book_id.lower():
            print(f"Warning: Book {i} ID '{book_id}' may be missing '$' character.")
            print("  Ensure IDs with '$' are quoted with single quotes in YAML.")

        resume = book.get('resume', True)
        if not isinstance(resume, bool):
            resume = str(resume).lower() in ('true', 'yes', '1')

        validated_books.append({
            'id': book_id,
            'start': int(book['start']),
            'end': int(book['end']),
            'output': str(book['output']),
            'resume': resume
        })

    # make sure no duplicate output files
    outputs = [b['output'] for b in validated_books]
    if len(outputs) != len(set(outputs)):
        print("Error: Duplicate output filenames found in YAML")
        sys.exit(1)

    return validated_books


def main():
    parser = argparse.ArgumentParser(
        description='Download HathiTrust books as PDF',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  %(prog)s -l "uc1.$c148966" -o "my_book.pdf"      # Download single book
  %(prog)s -l "uc1.$c148966" -b 1 -e 50            # Download page range
  %(prog)s -f books.yaml                            # Batch from YAML file
  %(prog)s -f books.yaml --safe                     # Batch with safe mode
        '''
    )
    parser.add_argument('-l', '--link', default=None, help='HathiTrust URL or book ID (required unless -f is used)')
    parser.add_argument('-f', '--file', default=None, help='YAML file for batch processing multiple books')
    parser.add_argument('-o', '--output', default=None, help='Output PDF filename')
    parser.add_argument('-b', '--begin', type=int, default=1, help='First page to download (default: 1)')
    parser.add_argument('-e', '--end', type=int, default=0, help='Last page to download (0 = all)')
    parser.add_argument('-p', '--pages', type=int, default=0, help='Total page count (if known, skips detection)')
    parser.add_argument('-k', '--keep', action='store_true', help='Keep individual page PDFs after merging')
    parser.add_argument('-v', '--verbose', action='store_true', help='Verbose output')
    parser.add_argument('-d', '--delay', type=float, default=5,
                        help='Minimum delay between requests in seconds (default: 5, max will be ~2x this)')
    parser.add_argument('--safe', action='store_true',
                        help='Safe mode: use longer delays between requests (10-20s instead of 5-12s) to avoid rate limiting')

    args = parser.parse_args()

    if not args.link and not args.file:
        parser.error("Either -l/--link or -f/--file is required")
    if args.link and args.file:
        parser.error("Cannot use both -l/--link and -f/--file")

    init_session()

    if args.file:
        process_batch(args)
    else:
        process_single_book(args)


def process_single_book(args, skip_merge=False, batch_mode=False, auto_resume=None):
    global shutdown_requested

    book_id = extract_book_id(args.link)
    if not book_id:
        print(f"Error: Could not extract book ID from: {args.link}")
        if batch_mode:
            return None
        sys.exit(1)

    if not batch_mode:
        print(f"Book ID: {book_id}")

    reset_download_counter()

    if not batch_mode:
        print("Initializing session...")
        try:
            # visit home page first to get cookies
            session.get("https://babel.hathitrust.org/", timeout=30)
            time.sleep(random.uniform(0.5, 1.5))

            # then visit the book page
            init_url = f"https://babel.hathitrust.org/cgi/pt?id={book_id}&seq=1"
            init_response = session.get(init_url, timeout=30)
            time.sleep(random.uniform(0.3, 1.0))

            if args.verbose:
                print(f"Session init status: {init_response.status_code}")
        except Exception as e:
            if args.verbose:
                print(f"Session init warning: {e}")
    else:
        try:
            init_url = f"https://babel.hathitrust.org/cgi/pt?id={book_id}&seq=1"
            session.get(init_url, timeout=30)
            time.sleep(random.uniform(0.3, 1.0))
        except Exception:
            pass

    if args.pages > 0:
        total_pages = args.pages
        print(f"Using provided page count: {total_pages}")
    elif args.end > 0:
        total_pages = args.end
        print(f"Using end page as total: {total_pages}")
    else:
        print("Detecting page count...")
        total_pages = get_page_count(book_id)

        if not total_pages:
            print("Could not detect page count via API, probing...")
            total_pages = get_page_count_by_probing(book_id)

        if not total_pages:
            print("\nError: Could not determine page count.")
            print("Please specify the page count manually with -p option.")
            print("You can find this by going to the last page of the book in your browser.")
            sys.exit(1)

        print(f"Detected {total_pages} pages")

    start_page = args.begin
    end_page = args.end if args.end > 0 else total_pages
    end_page = min(end_page, total_pages)

    print(f"Downloading pages {start_page} to {end_page}")

    # simulate browsing around the book a bit before downloading
    try:
        if args.verbose:
            print("Simulating initial browsing...")
        for _ in range(random.randint(1, 3)):
            browse_seq = random.randint(1, min(10, end_page))
            browse_url = f"https://babel.hathitrust.org/cgi/pt?id={book_id}&seq={browse_seq}"
            session.get(browse_url, timeout=30)
            time.sleep(random.uniform(0.5, 2.0))
    except Exception as e:
        if args.verbose:
            print(f"Browsing simulation warning: {e}")

    if args.safe:
        args.delay = 10
        if not batch_mode:
            print("Safe mode enabled: 10-20s delays")

    safe_book_id = sanitize_filename(book_id)
    temp_dir = os.path.join(os.getcwd(), f"hathitrust_temp_{safe_book_id}_{start_page}-{end_page}")

    pages_to_download = list(range(start_page, end_page + 1))
    downloaded_files = []
    failed_pages = []

    # check if we have a partial download already
    if os.path.exists(temp_dir):
        existing_files = list(Path(temp_dir).glob("page_*.pdf"))
        valid_existing = [(int(f.stem.split('_')[1]), str(f)) for f in existing_files if is_valid_pdf(str(f))]

        if valid_existing:
            existing_pages = set(seq for seq, _ in valid_existing)
            missing_pages = [p for p in pages_to_download if p not in existing_pages]

            print(f"\n{'='*50}")
            print(f"FOUND EXISTING DOWNLOAD")
            print(f"{'='*50}")
            print(f"Directory: {temp_dir}")
            print(f"Valid pages found: {len(valid_existing)}")
            print(f"Missing pages: {len(missing_pages)}")
            if missing_pages:
                print(f"Missing: {missing_pages[:20]}{'...' if len(missing_pages) > 20 else ''}")
            print(f"{'='*50}")

            if auto_resume is True:
                print("Auto-resuming download...")
                downloaded_files = valid_existing
                pages_to_download = missing_pages
                if not missing_pages:
                    print("No missing pages - all pages already downloaded!")
            elif auto_resume is False:
                print("Starting fresh (resume=false)...")
                for _, filepath in valid_existing:
                    try:
                        os.remove(filepath)
                    except:
                        pass
                for f in existing_files:
                    try:
                        os.remove(str(f))
                    except:
                        pass
                print(f"Deleted {len(existing_files)} files.")
            else:
                try:
                    print("\nOptions:")
                    print("  [R] Resume - download only missing pages")
                    print("  [S] Start fresh - delete existing and re-download all")
                    print("  [M] Merge now - merge existing pages into PDF")
                    print("  [Q] Quit")
                    response = input("\nChoice [R/s/m/q]: ").strip().lower()

                    if response == 'q':
                        print("Exiting.")
                        sys.exit(0)
                    elif response == 'm':
                        downloaded_files = valid_existing
                        pages_to_download = []
                        if missing_pages:
                            failed_pages = missing_pages
                    elif response == 's':
                        print("Deleting existing files...")
                        for _, filepath in valid_existing:
                            try:
                                os.remove(filepath)
                            except:
                                pass
                        for f in existing_files:
                            try:
                                os.remove(str(f))
                            except:
                                pass
                        print(f"Deleted {len(existing_files)} files.")
                    else:
                        downloaded_files = valid_existing
                        pages_to_download = missing_pages
                        if not missing_pages:
                            print("\nNo missing pages - all pages already downloaded!")

                except (EOFError, KeyboardInterrupt):
                    print("\n\nExiting.")
                    sys.exit(1)

    Path(temp_dir).mkdir(parents=True, exist_ok=True)

    delay_range = (args.delay, args.delay * 2.4)  # min and max delay

    if pages_to_download:
        print(f"Delay between pages: {delay_range[0]:.0f}-{delay_range[1]:.0f} seconds")
        print(f"\nDownloading {len(pages_to_download)} pages...")
        if not USE_CURL_CFFI:
            print("Warning: curl_cffi not installed, may get blocked. Install with: pip install curl_cffi")

        completed = 0
        start_time = time.time()
        downloaded_seqs = set(seq for seq, _ in downloaded_files)
        end_of_book_detected = False

        for seq in pages_to_download:
            if shutdown_requested or end_of_book_detected:
                break

            completed += 1

            try:
                filepath, success, actual_seq = download_page(book_id, seq, temp_dir, delay_range)
                if success:
                    # HathiTrust redirects to first page when you go past the end
                    if actual_seq != seq and actual_seq in downloaded_seqs:
                        print(f"\n\nEnd of book detected at page {actual_seq}")
                        print(f"(Requested page {seq} redirected to already-downloaded page {actual_seq})")
                        os.remove(filepath)

                        remaining = len([p for p in pages_to_download if p > seq])
                        if remaining > 0:
                            print(f"Remaining {remaining} pages would all be duplicates.")
                            try:
                                response = input("Stop downloading? [Y/n]: ").strip().lower()
                                if response != 'n':
                                    end_of_book_detected = True
                            except (EOFError, KeyboardInterrupt):
                                end_of_book_detected = True
                        else:
                            end_of_book_detected = True
                    else:
                        if actual_seq != seq:
                            actual_path = os.path.join(temp_dir, f"page_{actual_seq:05d}.pdf")
                            os.rename(filepath, actual_path)
                            filepath = actual_path
                            downloaded_files.append((actual_seq, filepath))
                            downloaded_seqs.add(actual_seq)
                        else:
                            downloaded_files.append((seq, filepath))
                            downloaded_seqs.add(seq)

                        if args.verbose:
                            if actual_seq != seq:
                                print(f"[{completed}/{len(pages_to_download)}] Downloaded page {seq} (redirected to {actual_seq})")
                            else:
                                print(f"[{completed}/{len(pages_to_download)}] Downloaded page {seq}")
                        else:
                            # progress bar
                            elapsed = time.time() - start_time
                            total = len(pages_to_download)
                            pct = completed * 100 // total

                            if completed > 0:
                                rate = completed / elapsed
                                remaining = total - completed
                                eta = remaining / rate if rate > 0 else 0
                                if eta > 60:
                                    eta_str = f"ETA: {int(eta//60)}m {int(eta%60)}s"
                                else:
                                    eta_str = f"ETA: {int(eta)}s"
                            else:
                                eta_str = "ETA: --"

                            bar_len = 20
                            filled = bar_len * completed // total
                            bar = '█' * filled + '░' * (bar_len - filled)
                            print(f"\r[{bar}] {pct}% ({completed}/{total}) {eta_str}  ", end='', flush=True)
                else:
                    failed_pages.append(seq)
            except Exception as e:
                print(f"\nError on page {seq}: {e}")
                failed_pages.append(seq)

        print()

    output_file = args.output or f"{sanitize_filename(book_id)}.pdf"

    if skip_merge:
        return {
            'book_id': book_id,
            'output_file': output_file,
            'downloaded_files': downloaded_files,
            'failed_pages': failed_pages,
            'temp_dir': temp_dir,
            'start_page': start_page,
            'end_page': end_page,
            'keep_files': args.keep
        }

    if shutdown_requested and pages_to_download:
        print(f"Download interrupted. {len(downloaded_files)} pages completed.")
        if downloaded_files:
            try:
                response = input("Merge downloaded pages into partial PDF? [Y/n]: ").strip().lower()
                if response == 'n':
                    print("Cleaning up temporary files...")
                    for seq, filepath in downloaded_files:
                        try:
                            os.remove(filepath)
                        except:
                            pass
                    try:
                        os.rmdir(temp_dir)
                    except:
                        pass
                    print("Cleanup complete.")
                    sys.exit(0)
            except (EOFError, KeyboardInterrupt):
                print("\nCleaning up and exiting...")
                for seq, filepath in downloaded_files:
                    try:
                        os.remove(filepath)
                    except:
                        pass
                try:
                    os.rmdir(temp_dir)
                except:
                    pass
                sys.exit(1)
        else:
            try:
                os.rmdir(temp_dir)
            except:
                pass
            sys.exit(0)

    # retry any failed pages
    while failed_pages and not shutdown_requested:
        print(f"\n{'='*50}")
        print(f"SKIPPED PAGES: {len(failed_pages)}")
        print(f"Pages: {failed_pages}")
        print(f"{'='*50}")

        try:
            response = input("\nRetry these pages? [Y/n]: ").strip().lower()
            if response == 'n':
                break

            retry_list = failed_pages[:]
            new_downloads, failed_pages = retry_failed_pages(retry_list, book_id, temp_dir, delay_range)
            downloaded_files.extend(new_downloads)

            if not failed_pages:
                print("\nAll pages recovered!")

        except (EOFError, KeyboardInterrupt):
            print("\n\nSkipping retries...")
            break

    downloaded_files.sort(key=lambda x: x[0])
    pdf_files = [f[1] for f in downloaded_files]

    if failed_pages:
        print(f"\n{'='*50}")
        print(f"FINAL SKIPPED PAGES: {len(failed_pages)}")
        print(f"Pages: {failed_pages}")
        print(f"{'='*50}")

        if pdf_files:
            try:
                response = input("\nMerge successfully downloaded pages into PDF? [Y/n]: ").strip().lower()
                if response == 'n':
                    print(f"\nDownloaded pages preserved in: {temp_dir}")
                    print("You can manually merge them later or re-run the script.")
                    sys.exit(0)
            except (EOFError, KeyboardInterrupt):
                print(f"\n\nDownloaded pages preserved in: {temp_dir}")
                sys.exit(1)
        else:
            print("No pages were downloaded successfully.")
            try:
                os.rmdir(temp_dir)
            except:
                pass
            sys.exit(1)

    if not pdf_files:
        print("No pages were downloaded successfully.")
        sys.exit(1)

    # merge all the PDFs
    print(f"\nMerging {len(pdf_files)} pages into {output_file}...")
    try:
        merge_pdfs(pdf_files, output_file)
        print(f"Successfully created: {output_file}")
        if failed_pages:
            print(f"NOTE: {len(failed_pages)} pages are missing from the PDF: {failed_pages}")
    except Exception as e:
        print(f"Error merging PDFs: {e}")
        print("Individual page PDFs are preserved in:", temp_dir)
        sys.exit(1)

    if not args.keep:
        print("Cleaning up temporary files...")
        for f in pdf_files:
            try:
                os.remove(f)
            except:
                pass
        try:
            os.rmdir(temp_dir)
        except:
            pass
    else:
        print(f"Page PDFs kept in: {temp_dir}")

    print("\nDone!")


def process_batch(args):
    global shutdown_requested

    books = load_books_yaml(args.file)

    print(f"\n{'='*50}")
    print(f"BATCH PROCESSING: {len(books)} books")
    print(f"{'='*50}")

    if args.safe:
        args.delay = 10
        print("Safe mode enabled: 10-20s delays")

    delay_range = (args.delay, args.delay * 2.4)

    print("\nInitializing session...")
    try:
        session.get("https://babel.hathitrust.org/", timeout=30)
        time.sleep(random.uniform(0.5, 1.5))
    except Exception as e:
        print(f"Warning: Session init failed: {e}")

    results = []
    for i, book in enumerate(books, 1):
        if shutdown_requested:
            print("\n\nShutdown requested. Stopping batch processing...")
            break

        print(f"\n{'='*50}")
        print(f"Book {i}/{len(books)}: {book['id']} -> {book['output']}")
        print(f"{'='*50}")

        book_args = argparse.Namespace(
            link=book['id'],
            output=book['output'],
            begin=book['start'],
            end=book['end'],
            pages=0,
            keep=args.keep,
            verbose=args.verbose,
            delay=args.delay,
            safe=args.safe
        )

        result = process_single_book(book_args, skip_merge=True, batch_mode=True, auto_resume=book['resume'])
        if result:
            results.append(result)
        else:
            print(f"  Skipping book due to error")

        # pause between books
        if i < len(books) and not shutdown_requested:
            wait = random.uniform(30, 60)
            print(f"\nWaiting {wait:.0f}s before next book...")
            time.sleep(wait)

    print(f"\n{'='*50}")
    print("BATCH DOWNLOAD SUMMARY")
    print(f"{'='*50}")

    for i, result in enumerate(results, 1):
        total = result['end_page'] - result['start_page'] + 1
        downloaded = len(result['downloaded_files'])
        missing = len(result['failed_pages'])

        print(f"\nBook {i}: {result['book_id']} -> {result['output_file']}")
        print(f"  Downloaded: {downloaded}/{total} pages")
        if missing:
            pages_str = str(result['failed_pages'][:10])
            if len(result['failed_pages']) > 10:
                pages_str = pages_str[:-1] + ", ...]"
            print(f"  Missing: {missing} pages: {pages_str}")
        else:
            print("  Status: COMPLETE")

    print(f"\n{'='*50}")

    for i, result in enumerate(results, 1):
        if shutdown_requested:
            break

        print(f"\n{'='*50}")
        print(f"Processing Book {i}/{len(results)}: {result['book_id']}")
        print(f"{'='*50}")

        failed_pages = result['failed_pages']
        downloaded_files = result['downloaded_files']
        temp_dir = result['temp_dir']
        book_id = result['book_id']

        while failed_pages and not shutdown_requested:
            print(f"\nSKIPPED PAGES: {len(failed_pages)}")
            print(f"Pages: {failed_pages}")

            try:
                response = input("\nRetry these pages? [Y/n]: ").strip().lower()
                if response == 'n':
                    break

                retry_list = failed_pages[:]
                new_downloads, failed_pages = retry_failed_pages(retry_list, book_id, temp_dir, delay_range)
                downloaded_files.extend(new_downloads)

                if not failed_pages:
                    print("\nAll pages recovered!")

            except (EOFError, KeyboardInterrupt):
                print("\n\nSkipping retries...")
                break

        result['failed_pages'] = failed_pages
        result['downloaded_files'] = downloaded_files

        downloaded_files.sort(key=lambda x: x[0])
        pdf_files = [f[1] for f in downloaded_files]

        if failed_pages:
            print(f"\nFINAL SKIPPED PAGES: {len(failed_pages)}")
            print(f"Pages: {failed_pages}")

            if pdf_files:
                try:
                    response = input("\nMerge successfully downloaded pages into PDF? [Y/n]: ").strip().lower()
                    if response == 'n':
                        print(f"\nPages preserved in: {temp_dir}")
                        continue
                except (EOFError, KeyboardInterrupt):
                    print(f"\n\nPages preserved in: {temp_dir}")
                    continue
            else:
                print("No pages downloaded successfully.")
                continue

        if pdf_files:
            output_file = result['output_file']
            print(f"\nMerging {len(pdf_files)} pages into {output_file}...")
            try:
                merge_pdfs(pdf_files, output_file)
                print(f"Successfully created: {output_file}")
                if failed_pages:
                    print(f"NOTE: {len(failed_pages)} pages are missing: {failed_pages}")

                if not result['keep_files']:
                    for f in pdf_files:
                        try:
                            os.remove(f)
                        except:
                            pass
                    try:
                        os.rmdir(temp_dir)
                    except:
                        pass
                else:
                    print(f"Page PDFs kept in: {temp_dir}")

            except Exception as e:
                print(f"Error merging PDFs: {e}")
                print(f"Pages preserved in: {temp_dir}")

    print(f"\n{'='*50}")
    print("BATCH PROCESSING COMPLETE")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
