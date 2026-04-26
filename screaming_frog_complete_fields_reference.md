# Screaming Frog SEO Spider - Complete Fields/Columns Reference
# For Building a Crawler That Replicates Screaming Frog's Data Extraction
# Last Updated: 2026-04-26

---

## 1. INTERNAL TAB

### Columns (approx. 55-65 default columns)

**Core URL Data:**
- Address
- Content (content type: text/html, text/css, application/javascript, image/jpeg, etc.)
- Status Code (HTTP response code: 200, 301, 302, 404, 500, etc.)
- Status (HTTP header response text: OK, Moved Permanently, Not Found, etc.)
- Indexability (Indexable / Non-Indexable)
- Indexability Status (reason for Non-Indexable: Canonicalised, Noindex, etc.)

**Page Titles:**
- Title 1
- Title 1 Length (character count)
- Title 1 Pixel Width (SERP pixel width)
- Title 2 (if multiple title tags exist)
- Title 2 Length
- Title 2 Pixel Width

**Meta Descriptions:**
- Meta Description 1
- Meta Description 1 Length
- Meta Description 1 Pixel Width
- Meta Description 2 (if multiple)
- Meta Description 2 Length

**Meta Keywords:**
- Meta Keyword 1
- Meta Keywords 1 Length
- Meta Keyword 2
- Meta Keywords 2 Length

**Headings:**
- h1-1 (first H1 text)
- h1-Len-1 (first H1 character length)
- h1-2 (second H1 if multiple)
- h1-Len-2
- h2-1 (first H2 text)
- h2-Len-1 (first H2 character length)
- h2-2 (second H2 if multiple)
- h2-Len-2

**Directives:**
- Meta Robots 1 (e.g., index, noindex, follow, nofollow, none, noarchive, nosnippet)
- Meta Robots 2 (if multiple meta robots tags)
- X-Robots-Tag 1 (HTTP header X-Robots-Tag value)
- X-Robots-Tag 2
- Meta Refresh 1 (meta refresh redirect value)

**Canonicals & Pagination:**
- Canonical Link Element 1 (rel="canonical" URL)
- Canonical Link Element 2
- rel="next" 1
- rel="prev" 1
- HTTP rel="next" 1 (Link header)
- HTTP rel="prev" 1 (Link header)

**Size & Performance:**
- Size (uncompressed HTML size in bytes)
- Transferred (compressed bytes transferred)
- Total Transferred (total bytes including all sub-resources in JS rendering mode)
- Response Time (seconds to download)

**Content Metrics:**
- Word Count (words inside body tag, excluding nav/footer by default)
- Text Ratio (text-to-HTML ratio percentage)
- Closest Similarity Match (URL of the nearest duplicate)
- No. Near Duplicates (count of near-duplicate pages)
- Spelling Errors (count)
- Grammar Errors (count)
- Language (detected/configured language)
- Hash (MD5 hash of page content for exact duplicate detection)

**Link Metrics:**
- Inlinks (total internal inlinks to this URL)
- Unique Inlinks (deduplicated internal inlinks)
- Unique JS Inlinks (unique inlinks found via JavaScript rendering)
- % of Total (percentage of total internal links pointing to this URL)
- Outlinks (total internal outlinks from this URL)
- Unique Outlinks (deduplicated internal outlinks)
- Unique JS Outlinks (unique outlinks found via JavaScript rendering)
- External Outlinks (total external outlinks)
- Unique External Outlinks (deduplicated external outlinks)
- Unique External JS Outlinks (external outlinks found via JS rendering)

**Crawl Metrics:**
- Crawl Depth (number of clicks from start URL)
- Folder Depth (number of subfolders in URL path)
- Link Score (0-100, internal PageRank-like metric)

**Redirect Data:**
- Redirect URI (target URL if this URL redirects)
- Redirect Type (HTTP Redirect, JavaScript Redirect, Meta Refresh, etc.)

**Technical:**
- Last-Modified (Last-Modified HTTP header value)
- HTTP Version (HTTP/1.0, HTTP/1.1, HTTP/2, etc.)
- URL Encoded Address (percent-encoded version of the URL)

### Filters
- HTML
- JavaScript
- CSS
- Images
- PDF
- Flash
- Other
- Unknown

---

## 2. EXTERNAL TAB

### Columns
- Address (external URL)
- Content (content type)
- Status Code
- Status
- Crawl Depth
- Inlinks (number of internal pages linking to this external URL)

### Filters
- HTML
- JavaScript
- CSS
- Images
- PDF
- Flash
- Other
- Unknown

---

## 3. RESPONSE CODES TAB

### Columns
- Address
- Content
- Status Code
- Status
- Indexability
- Indexability Status
- Inlinks
- Response Time
- Redirect URL (target of redirect)
- Redirect Type

### Filters
- Blocked by Robots.txt
- Blocked Resource
- No Response
- Success (2XX)
- Redirection (3XX)
- Redirection (JavaScript)
- Redirection (Meta Refresh)
- Redirection (HTTP Refresh)
- Redirect Chain
- Redirect Loop
- Client Error (4XX)
- Server Error (5XX)

---

## 4. PAGE TITLES TAB

### Columns
- Address
- Occurrences (number of title tags found)
- Title 1
- Title 1 Length
- Title 1 Pixel Width
- Title 2
- Title 2 Length
- Title 2 Pixel Width
- Indexability
- Indexability Status

### Filters
- All
- Missing
- Duplicate
- Over 60 Characters
- Below 30 Characters
- Over X Pixels (configurable, default 580px)
- Below X Pixels (configurable, default 200px)
- Same as H1
- Multiple
- Outside <head>

---

## 5. META DESCRIPTION TAB

### Columns
- Address
- Occurrences
- Meta Description 1
- Meta Description 1 Length
- Meta Description 1 Pixel Width
- Meta Description 2
- Meta Description 2 Length
- Meta Description 2 Pixel Width
- Indexability
- Indexability Status

### Filters
- All
- Missing
- Duplicate
- Over 155 Characters
- Below 70 Characters
- Over X Pixels (configurable)
- Below X Pixels (configurable)
- Multiple
- Outside <head>

---

## 6. META KEYWORDS TAB

### Columns
- Address
- Occurrences
- Meta Keyword 1
- Meta Keyword 1 Length
- Meta Keyword 2
- Meta Keyword 2 Length
- Indexability
- Indexability Status

### Filters
- All
- Missing
- Duplicate
- Multiple

---

## 7. H1 TAB

### Columns
- Address
- Occurrences (number of H1 tags found)
- h1-1
- h1-Len-1
- h1-2
- h1-Len-2
- Indexability
- Indexability Status

### Filters
- All
- Missing
- Duplicate
- Over 70 Characters
- Multiple
- Alt Text in H1 (H1 contains only an image with alt text)
- Non-Sequential (H1 not the first heading level)

---

## 8. H2 TAB

### Columns
- Address
- Occurrences
- h2-1
- h2-Len-1
- h2-2
- h2-Len-2
- Indexability
- Indexability Status

### Filters
- All
- Missing
- Duplicate
- Over 70 Characters
- Multiple
- Non-Sequential

---

## 9. IMAGES TAB

### Columns
- Address (image URL)
- Content (image MIME type: image/jpeg, image/png, image/webp, etc.)
- Size (image file size in bytes)
- Indexability
- Indexability Status

### Lower Window - Image Details Tab (per selected image)
- From (source page URL referencing the image)
- Alt Text (alt attribute value for each occurrence)
- Anchor (if image is within a link)
- Type (IMG, CSS Background, etc.)

### Filters
- All
- Over 100KB (configurable threshold)
- Missing Alt Text (alt attribute present but empty)
- Missing Alt Attribute (no alt attribute at all)
- Alt Text Over 100 Characters
- Background Images
- Missing Size Attributes (missing width/height attributes)
- Incorrectly Sized Images

---

## 10. CANONICALS TAB

### Columns
- Address
- Occurrences (number of canonical tags found)
- Indexability
- Indexability Status
- Canonical Link Element 1 (HTML rel="canonical" value)
- Canonical Link Element 2
- HTTP Canonical 1 (Link header canonical)
- HTTP Canonical 2
- Meta Robots 1
- Meta Robots 2
- X-Robots-Tag 1
- X-Robots-Tag 2
- rel="next" 1
- rel="prev" 1

### Filters
- All
- Contains Canonical
- Self Referencing
- Canonicalised (points to a different URL)
- Missing
- Multiple
- Multiple Conflicting
- Non-Indexable Canonical
- Canonical Is Relative
- Unlinked
- Invalid Attribute In Annotation
- Contains Fragment URL
- Outside <head>

---

## 11. DIRECTIVES TAB (Meta Robots / X-Robots)

### Columns
- Address
- Indexability
- Indexability Status
- Meta Robots 1
- Meta Robots 2
- X-Robots-Tag 1
- X-Robots-Tag 2
- Meta Refresh 1

### Filters
- All
- Index
- Noindex
- Follow
- Nofollow
- None
- NoArchive
- NoSnippet
- Max-Snippet
- Max-Image-Preview
- Max-Video-Preview
- NoODP
- NoYDIR
- NoImageIndex
- NoTranslate
- Unavailable_After
- Refresh
- Outside <head>

---

## 12. HREFLANG TAB

### Columns
- Address
- Occurrences
- Title 1
- Title 2
- Indexability
- Indexability Status
- HTML hreflang 1 (language-region code, e.g., en-us)
- HTML hreflang 1 URL (target URL for that hreflang)
- HTML hreflang 2
- HTML hreflang 2 URL
- (continues for as many hreflang annotations as found)
- HTTP hreflang 1 (from Link HTTP header)
- HTTP hreflang 1 URL
- HTTP hreflang 2
- HTTP hreflang 2 URL
- Sitemap hreflang 1 (from XML sitemap)
- Sitemap hreflang 1 URL
- Sitemap hreflang 2
- Sitemap hreflang 2 URL

### Lower Window - Hreflang Details
- Source URL
- Language-Region Code
- Target URL
- Confirmation Status (Confirmed, Missing, Inconsistent, Not Canonical, etc.)

### Filters
- All
- Contains Hreflang
- Non-200 Hreflang URLs
- Unlinked Hreflang URLs
- Missing Return Links
- Inconsistent Language & Region Return Links
- Non-Canonical Return Links
- Noindex Return Links
- Incorrect Language & Region Codes
- Multiple Entries
- Missing Self Reference
- Not Using Canonical
- Missing X-Default
- Missing
- Outside <head>

---

## 13. STRUCTURED DATA TAB

### Columns
- Address
- Status Code
- Indexability
- Indexability Status
- Total Items (total structured data items/types found)
- Unique Types (count of distinct schema types)
- Validation Errors (Schema.org validation error count)
- Validation Warnings (Schema.org validation warning count)
- Rich Result Errors (Google rich result validation errors)
- Rich Result Warnings (Google rich result validation warnings)
- (Dynamic columns for each schema type found, e.g., Product, Article, FAQ, etc.)

### Lower Window - Structured Data Details
- Type (e.g., Product, Article, BreadcrumbList, FAQ, etc.)
- Implementation (JSON-LD, Microdata, RDFa)
- Properties and values
- Validation Type (Schema.org or specific Google rich result feature)
- Severity (Error, Warning, Info)
- Message (description of the specific validation issue)

### Filters
- All
- Contains Structured Data
- Missing Structured Data
- Validation Errors
- Validation Warnings
- Parse Errors
- Microdata URLs
- JSON-LD URLs
- RDFa URLs
- Rich Result Validation Errors
- Rich Result Validation Warnings

---

## 14. LINKS TAB (Inlinks / Outlinks - Lower Window Panels)

### Inlinks Tab Columns (lower window, per selected URL)
- From (source page URL containing the link)
- To (destination URL)
- Type (Hyperlink, IMG, JavaScript, CSS, Redirect, Canonical, Hreflang, Pagination, Meta Refresh, HTTP Refresh, AMPHTML, Alternate, Sitemap, etc.)
- Anchor Text (link text)
- Alt Text (image alt text if link is an image)
- Follow (true/false - whether link is followed)
- Rel (rel attribute values: nofollow, noopener, noreferrer, ugc, sponsored, etc.)
- Target (target attribute: _blank, _self, _parent, _top)
- Path Type (Href, JS, Redirect, etc.)
- Link Position (Navigation, Header, Footer, Sidebar/Aside, Content)
- Link Path (XPath of the link element in the DOM)
- Status Code (of the destination URL)

### Outlinks Tab Columns (lower window, per selected URL)
- From
- To
- Type
- Anchor Text
- Alt Text
- Follow
- Rel
- Target
- Path Type
- Link Position
- Link Path
- Status Code

### Bulk Export Options
- All Inlinks
- All Outlinks
- All Anchor Text

---

## 15. RESPONSE TIMES (column in Internal/Response Codes tabs)

The Response Time data appears as a column in the Internal tab and Response Codes tab rather than as a separate dedicated tab.

### Fields
- Response Time (seconds to download the URL, measured during the crawl)

### Response Time Filters (via Internal tab)
- Slow responses can be identified by sorting the Response Time column
- Custom extraction can provide additional timing data

---

## 16. SECURITY TAB

### Columns
- Address
- Content
- Status Code
- Status
- Indexability
- Indexability Status
- Canonical Link Element 1
- Canonical Link Element 2
- Meta Robots 1
- Meta Robots 2
- X-Robots-Tag 1
- X-Robots-Tag 2

### Filters
- HTTP URLs (insecure URLs that should be HTTPS)
- HTTPS URLs
- Mixed Content (HTTPS pages loading HTTP resources)
- Form URL Insecure (forms with HTTP action URLs)
- Form on HTTP URL
- Unsafe Cross-Origin Links (target="_blank" without rel="noopener")
- Protocol-Relative Resource Links (e.g., //example.com/script.js)
- Missing HSTS Header (HTTP Strict-Transport-Security)
- Missing Content-Security-Policy Header
- Missing X-Content-Type-Options Header
- Missing X-Frame-Options Header
- Missing Secure Referrer-Policy Header
- Bad Content Type

---

## 17. PAGESPEED TAB (requires PageSpeed Insights API integration)

### Lighthouse Lab Metrics Columns
- PSI Status
- PSI Error
- Performance Score (0-100)
- Time to First Byte (TTFB)
- First Contentful Paint Time (FCP)
- First Contentful Paint Score
- Speed Index Time
- Speed Index Score
- Largest Contentful Paint Time (LCP)
- Largest Contentful Paint Score
- Time to Interactive (TTI)
- Time to Interactive Score
- First Meaningful Paint Time (FMP) [removed in v23]
- First Meaningful Paint Score [removed in v23]
- Max Potential First Input Delay
- Max Potential First Input Delay Score
- Total Blocking Time (TBT)
- Total Blocking Time Score
- Cumulative Layout Shift (CLS)
- Cumulative Layout Shift Score

### Resource Breakdown Columns
- Total Requests
- Total Page Size
- HTML Size
- HTML Count
- Image Size
- Image Count
- CSS Size
- CSS Count
- JavaScript Size
- JavaScript Count
- Font Size
- Font Count
- Media Size
- Media Count
- Other Size
- Other Count
- Third Party Size
- Third Party Count

### CrUX (Chrome User Experience Report) Columns
- Core Web Vitals Assessment (Pass/Fail)
- CrUX First Contentful Paint Time
- CrUX First Contentful Paint Category (Good/Needs Improvement/Poor)
- CrUX First Input Delay Time [deprecated, replaced by INP]
- CrUX First Input Delay Category
- CrUX Interaction to Next Paint (INP) [added in newer versions]
- CrUX Interaction to Next Paint Category
- CrUX Largest Contentful Paint Time
- CrUX Largest Contentful Paint Category
- CrUX Cumulative Layout Shift
- CrUX Cumulative Layout Shift Category

### CrUX Origin-Level Columns
- Origin Core Web Vitals Assessment
- CrUX Origin First Contentful Paint Time
- CrUX Origin First Contentful Paint Category
- CrUX Origin First Input Delay Time
- CrUX Origin First Input Delay Category
- CrUX Origin Interaction to Next Paint
- CrUX Origin Interaction to Next Paint Category
- CrUX Origin Largest Contentful Paint Time
- CrUX Origin Largest Contentful Paint Category
- CrUX Origin Cumulative Layout Shift
- CrUX Origin Cumulative Layout Shift Category

### Optimization Opportunities & Diagnostics Columns
- Total Size Savings (bytes)
- Total Time Savings (ms)
- Render Blocking Requests Savings (renamed from "Eliminate Render-Blocking Resources" in v23)
- Defer Offscreen Images Savings (time) [removed in v23]
- Defer Offscreen Images Savings (bytes) [removed in v23]
- Improve Image Delivery Savings (time) [new in v23, consolidates image audits]
- Improve Image Delivery Savings (bytes) [new in v23]
- Efficiently Encode Images Savings (time) [removed/consolidated in v23]
- Efficiently Encode Images Savings (bytes) [removed/consolidated in v23]
- Properly Size Images Savings (time) [removed/consolidated in v23]
- Properly Size Images Savings (bytes) [removed/consolidated in v23]
- Minify CSS Savings (time)
- Minify CSS Savings (bytes)
- Minify JavaScript Savings (time)
- Minify JavaScript Savings (bytes)
- Reduce Unused CSS Savings (time)
- Reduce Unused CSS Savings (bytes)
- Reduce Unused JavaScript Savings (time)
- Reduce Unused JavaScript Savings (bytes)
- Serve Images in Next-Gen Formats Savings (time) [removed/consolidated in v23]
- Serve Images in Next-Gen Formats Savings (bytes) [removed/consolidated in v23]
- Enable Text Compression Savings (time) [removed/consolidated in v23]
- Enable Text Compression Savings (bytes) [removed/consolidated in v23]
- Preconnect to Required Origins Savings
- Document Request Latency [new in v23, replaces Server Response Times]
- Server Response Times (TTFB) [removed in v23]
- Server Response Times Category
- Multiple Redirects Savings [removed/consolidated in v23]
- Preload Key Requests Savings [removed in v23]
- Use Video Format for Animated Images Savings (time) [removed/consolidated in v23]
- Use Video Format for Animated Images Savings (bytes) [removed/consolidated in v23]
- Total Image Optimization Savings
- Legacy JavaScript Savings (renamed from "Avoid Serving Legacy JavaScript to Modern Browsers" in v23)
- Optimize DOM Size (renamed from "Avoid Excessive DOM Size" in v23)
- DOM Element Count
- JavaScript Execution Time
- JavaScript Execution Time Category
- Use Efficient Cache Lifetimes Savings (renamed from "Efficient Cache Policy" in v23)
- Minimize Main-Thread Work (time)
- Minimize Main-Thread Work Category
- Font Display (renamed from "Ensure Text Remains Visible During WebFont Load" in v23)
- Image Elements Do Not Have Explicit Width & Height
- Layout Shift Culprits (renamed from "Avoid Large Layout Shifts" in v23)
- LCP Request Discovery [new in v23]
- Forced Reflow [new in v23]
- Avoid Enormous Network Payloads [new in v23]
- Network Dependency Tree [new in v23]
- Duplicated JavaScript [new in v23]

### PageSpeed Filters
- All
- Opportunities (pages with optimization opportunities)
- Diagnostics (pages with diagnostic issues)
- Good (Performance score 90-100)
- Needs Improvement (Performance score 50-89)
- Poor (Performance score 0-49)
- CrUX Pass
- CrUX Fail

---

## 18. AMP TAB

### Columns
- Address (AMP URL)
- Content
- Status Code
- Status
- Occurrences (canonical tag count)
- Indexability
- Indexability Status
- Canonical Link Element 1
- Crawl Depth
- Response Time
- AMP Canonical URL (the non-AMP canonical)

### Filters (17 total)
**SEO Filters:**
- Non-200 Response
- Missing Non-AMP Return Link
- Missing Canonical to Non-AMP
- Non-Indexable Canonical
- Indexable
- Non-Indexable

**AMP Specification Compliance Filters:**
- Missing HTML AMP Tag
- Missing/Invalid Doctype HTML Tag
- Missing Head Tag
- Missing Body Tag
- Missing Canonical
- Missing/Invalid Meta Charset Tag
- Missing/Invalid Meta Viewport Tag
- Missing/Invalid AMP Script
- Missing/Invalid AMP Boilerplate
- Contains Disallowed HTML
- Other Validation Errors

---

## 19. CONTENT TAB

### Columns
- Address
- Word Count (words in the body, excluding nav/footer by default)
- Average Words Per Sentence
- Flesch Reading Ease Score (0-100 readability metric)
- Readability (Easy, Fairly Easy, Standard, Fairly Difficult, Difficult, Very Difficult)
- Closest Similarity Match (URL of nearest duplicate)
- No. Near Duplicates (count of near-duplicate pages)
- Total Language Errors (spelling + grammar)
- Spelling Errors (count)
- Grammar Errors (count)
- Language (detected or configured language code)
- Hash (MD5 hash of page content)
- Indexability
- Indexability Status
- Text Ratio (text-to-code ratio)

### Filters
- All
- Exact Duplicates
- Near Duplicates
- Low Content Pages (configurable threshold, default below 200 words)
- Soft 404 Pages
- Spelling Errors
- Grammar Errors
- Readability Difficult
- Readability Very Difficult
- Lorem Ipsum Placeholder

---

## ADDITIONAL TABS

### URL TAB
**Columns:**
- Address
- Content
- Status Code
- Status
- Indexability
- Indexability Status
- Hash
- Length (URL character length)
- Canonical 1
- URL Encoded Address

**Filters:**
- Non ASCII Characters
- Underscores
- Uppercase
- Multiple Slashes
- Repetitive Path
- Contains A Space
- Internal Search
- Parameters
- Broken Bookmark
- GA Tracking Parameters
- Over 115 Characters

### PAGINATION TAB
**Columns:**
- Address
- Occurrences
- Indexability
- Indexability Status
- rel="next" 1
- rel="prev" 1
- Canonical Link Element 1
- Canonical Link Element 2
- HTTP Canonical 1
- HTTP Canonical 2
- Meta Robots 1
- Meta Robots 2
- X-Robots-Tag 1
- X-Robots-Tag 2

**Filters:**
- Contains Pagination
- First Page
- Paginated 2+ Pages
- Pagination URL Not In Anchor Tag
- Non-200 Pagination URL
- Unlinked Pagination URL
- Non-Indexable
- Multiple Pagination URLs
- Pagination Loop
- Sequence Error

### JAVASCRIPT TAB (JavaScript Rendering mode)
**Columns:**
- Address
- Status Code
- Status
- HTML Word Count
- Rendered Word Count
- HTML Headings
- Rendered Headings
- HTML Inlinks
- Rendered Inlinks
- HTML Outlinks
- Rendered Outlinks
- Blocked Resources
- Render Time

**Filters:**
- Blocked Resources
- High Render Time
- HTML Word Count vs Rendered Word Count
- JavaScript Rendering Issues

### SITEMAPS TAB
**Columns:**
- Address
- Status Code
- Status
- Indexability
- In Sitemap (whether URL is found in XML sitemap)
- Sitemap URL (which sitemap references this URL)

**Filters:**
- URLs in Sitemap
- URLs Not in Sitemap
- Orphan URLs
- Non-Indexable URLs in Sitemap
- XML Sitemap Issues

---

## BULK EXPORT OPTIONS

The following data can be bulk-exported beyond individual tab exports:

### Response Codes
- Redirect Chains
- Redirect & Canonical Chains
- All Redirects
- All 3XX Inlinks
- All 4XX Inlinks
- All 5XX Inlinks
- All Error Inlinks (new in v23)
- Redirects to Error (new in v23)

### Links
- All Inlinks
- All Outlinks
- All Anchor Text

### Images
- All Image Inlinks (with source pages, alt text)
- Missing Alt Text
- Missing Alt Attributes

### Security
- Mixed Content (HTTP resources on HTTPS pages)
- Insecure Form URLs
- Unsafe Cross-Origin Links
- Protocol-Relative Links

### Hreflang
- Non-200 Hreflang URLs
- Unlinked Hreflang URLs
- Missing Return Links
- All Hreflang URLs

### Structured Data
- Validation Errors
- Validation Warnings
- All Structured Data

### Sitemaps
- URLs in Sitemap
- Orphan URLs
- URLs Not in Sitemap

### PageSpeed
- All PageSpeed Data
- Opportunities
- Diagnostics

---

## NOTES FOR BUILDING A CRAWLER REPLICATION

1. **Column count varies**: The Internal tab can have 55-65+ columns depending on configuration and what features are enabled.

2. **Dynamic columns**: Title, Meta Description, Meta Keywords, H1, H2, Meta Robots, X-Robots-Tag, and Canonical columns expand (Title 1, Title 2, Title 3...) if multiple tags are found on a page.

3. **Hreflang columns are highly dynamic**: They expand based on the number of hreflang annotations found (can be dozens per page).

4. **PageSpeed requires API**: The PageSpeed tab data comes from Google's PageSpeed Insights API, not from the crawler itself. You would need to integrate with that API.

5. **CrUX data requires API**: Chrome User Experience Report data also comes from an external API.

6. **JavaScript rendering**: Many columns have both HTML-only and rendered variants when JavaScript rendering is enabled.

7. **Structured Data columns are dynamic**: They expand based on the schema types discovered across the crawl.

8. **Link data is relational**: Inlinks/Outlinks represent relationships between URLs and are best stored in a separate table/entity.

9. **Pixel width calculations**: Title and Meta Description pixel widths are calculated based on Google's SERP font (Arial 20px for titles, Arial 14px for descriptions, approximately).

10. **Configurable thresholds**: Many filters have configurable thresholds (e.g., "Over 100KB" for images, "Over 60 characters" for titles).
