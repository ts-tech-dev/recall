"""SQLite schema of the index. Bump SCHEMA_VERSION when it changes; REBUILT_TABLES are then recreated."""

SCHEMA_VERSION = "2"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS docs(
    id INTEGER PRIMARY KEY, path TEXT UNIQUE, title TEXT, ext TEXT, folder TEXT, kind TEXT,
    mtime REAL, size INTEGER, tags TEXT, error TEXT, indexed_at REAL);
CREATE TABLE IF NOT EXISTS chunks(
    id INTEGER PRIMARY KEY, doc_id INTEGER, ord INTEGER, heading TEXT, anchor TEXT,
    text TEXT, images TEXT);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(doc_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    title, heading, text, tags, tokenize='porter unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS vectors(chunk_id INTEGER PRIMARY KEY, vec BLOB);
CREATE TABLE IF NOT EXISTS links(src_id INTEGER, dst_path TEXT);
CREATE INDEX IF NOT EXISTS links_src ON links(src_id);
CREATE INDEX IF NOT EXISTS links_dst ON links(dst_path);
CREATE TABLE IF NOT EXISTS doc_images(doc_id INTEGER, key TEXT, path TEXT);
CREATE INDEX IF NOT EXISTS doc_images_doc ON doc_images(doc_id);
CREATE INDEX IF NOT EXISTS doc_images_key ON doc_images(key);
CREATE TABLE IF NOT EXISTS image_meta(
    key TEXT PRIMARY KEY, ocr TEXT, ocr_done INTEGER DEFAULT 0, caption TEXT, caption_model TEXT);
"""
# Tables rebuilt when the schema changes; image_meta (OCR/caption cache) survives.
REBUILT_TABLES = ("docs", "chunks", "chunks_fts", "vectors", "links", "doc_images")
