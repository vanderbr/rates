# IRS Revenue Ruling Source Archive

This directory preserves IRS PDF source materials used for AFR and Section
7520 rate history. The goal is simple: keep the underlying revenue rulings easy
to find, cite, and review, even when older IRS pages are difficult to navigate.

Files are grouped by the calendar year of the rate month:

```text
sources/irs-revenue-rulings/
sources/irs-revenue-rulings/manifest.json
sources/irs-revenue-rulings/INDEX.md
sources/irs-revenue-rulings/by-year/YYYY/YYYY-MM_subject_publication_original.pdf
```

The file naming pattern is:

```text
YYYY-MM_subject_publication_original.pdf
```

For example:

```text
2026-04_afr-7520_rev-rul-2026-7_rr-26-07.pdf
1996-01_7520_rev-rul-96-6-irb-1996-2_irb96-02.pdf
```

`INDEX.md` is the reader-friendly guide. It lists the rate month, whether the
PDF supports AFR, Section 7520, or both, the IRS publication title, the local
PDF, the IRS URL, and the retrieval date.

`manifest.json` keeps the same information in structured form, with byte counts
and SHA-256 checksums for anyone who wants to verify the archived files.

This archive is for original IRS PDFs from `www.irs.gov`, especially files under
`/pub/irs-drop/` and `/pub/irs-irbs/`.

Older AFR and Section 7520 rulings may appear in bound Internal Revenue
Cumulative Bulletins instead of stand-alone monthly PDFs. Known 1989-1995
source leads are tracked in `historical-source-leads.json`. Those leads point
to official GovInfo Treasury/IRS catalog records and note Internet Archive scan
records where found. The large bound-volume PDFs are not copied here unless
they can be retrieved, verified, and stored in a form that fits normal GitHub
repository limits. For those years, use the leads as the starting point for
finding exact IRS-published ruling pages; do not backfill values from OCR or
secondary summaries without checking the published ruling.

Use:

```sh
python3 scripts/archive_irs_pdf_source.py \
  --year 2026 \
  --period 2026-04 \
  --subject afr \
  --subject section-7520-rates \
  --url https://www.irs.gov/pub/irs-drop/rr-26-07.pdf \
  --title "Rev. Rul. 2026-7"
```

If the PDF has already been downloaded and checked locally, pass it with
`--input-pdf` and keep the IRS URL in `--url`:

```sh
python3 scripts/archive_irs_pdf_source.py \
  --year 2026 \
  --period 2026-04 \
  --subject afr \
  --subject section-7520-rates \
  --url https://www.irs.gov/pub/irs-drop/rr-26-07.pdf \
  --title "Rev. Rul. 2026-7" \
  --input-pdf /path/to/rr-26-07.pdf
```

The script writes the PDF under `by-year/YYYY/` and refreshes both `INDEX.md`
and `manifest.json`.
