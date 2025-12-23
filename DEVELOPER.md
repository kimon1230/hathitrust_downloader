# Developer Notes

## TODOs / Future Improvements

### Rate Limiting
- **Smarter failure handling**: Currently gives up after 6 consecutive failures. Could implement adaptive behavior based on error type or time of day.
  - Location: `download_page()` function around line 299
  - Consider: Different thresholds for 429 vs 403 errors

### Disk Space Monitoring
- Currently checks every 10 downloads during execution
- Could make frequency configurable
- Consider adding option to specify minimum space requirement

### PDF Processing
- **Add compression option**: Output PDFs can be quite large. Consider adding optional compression.
  - Location: `merge_pdfs()` function around line 391
  - Libraries to consider: `pypdf` has some compression features, or could use `pikepdf`

### Performance
- **Parallel downloads**: Could download multiple pages concurrently with careful rate limiting
  - Would need thread-safe session management
  - Consider: asyncio with aiohttp or concurrent.futures with thread pool

### User Experience
- **Progress persistence**: Save progress state to file so resume works even if temp dir is deleted
  - Could save JSON file with download state
  - Resume from bookmark file

## Architecture Notes

### Session Management
- Global session object is used throughout
- `curl_cffi` provides better browser impersonation when available
- Session is reset after 3 consecutive failures to get fresh cookies

### Rate Limiting Strategy
- Random delays between requests (default 5-12s)
- Extra pauses every 10-20 downloads
- Gaussian jitter to avoid predictable patterns
- Exponential backoff on failures

### Page Count Detection
Falls back through multiple strategies:
1. HathiTrust API (STRUCT1 endpoint)
2. HTML parsing with multiple regex patterns
3. Binary search probing (slowest but most reliable)

## Known Issues

### HathiTrust Quirks
- Books redirect to page 1 when you request beyond the last page
- Some books have access restrictions that aren't detectable upfront
- Page count API sometimes returns stale data
- HTML structure changes periodically (hence multiple regex patterns)

### Concurrent Execution
- Single global lock file prevents multiple instances from running simultaneously
- Lock file location: `/tmp/hathitrust_downloader.lock` on Unix, `%TEMP%\hathitrust_downloader.lock` on Windows
- Stale locks (from crashes) are automatically detected and cleaned up using PID checking
- Lock is released via `atexit` handler on normal exit and signal handler on CTRL+C

## Testing

No automated tests currently. Manual testing workflow:
1. Test single page download
2. Test page range
3. Test full book download
4. Test resume functionality
5. Test batch YAML processing
6. Test safe mode with rate limiting

## Code Style

- Inline comments for tricky parts
- Keep user-facing messages professional
- Internal comments can be casual
- Globals prefixed with `_` for internal state
