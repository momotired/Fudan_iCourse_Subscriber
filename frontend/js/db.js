/**
 * sql.js wrapper — load, query, and export the iCourse SQLite database.
 * Sets window.ICS.db global.
 */

window.ICS = window.ICS || {};

const _SQL_CDN = "https://cdnjs.cloudflare.com/ajax/libs/sql.js/1.12.0";
let _db = null;

async function _initDB(dbBytes) {
  const SQL = await window.initSqlJs({
    locateFile: (file) => `${_SQL_CDN}/${file}`,
  });
  _db = dbBytes ? new SQL.Database(dbBytes) : new SQL.Database();
}

function _ensureSchema() {
  if (!_db) return;
  const existing = new Set(
    (_db.exec("PRAGMA table_info(lectures)")[0]?.values || []).map((r) => r[1])
  );
  for (const [col, typedef] of [
    ["error_msg", "TEXT"],
    ["error_count", "INTEGER DEFAULT 0"],
    ["error_stage", "TEXT"],
    ["summary_model", "TEXT"],
  ]) {
    if (!existing.has(col)) _db.run(`ALTER TABLE lectures ADD COLUMN ${col} ${typedef}`);
  }
}

function _deriveState(row) {
  if (row.error_stage) return "failed";
  if (row.summary && row.processed_at) return "ready";
  if (row.transcript && !row.summary) return "processing";
  return "waiting";
}

function _queryAll(sql, params) {
  if (!_db) return [];
  const stmt = _db.prepare(sql);
  if (params) stmt.bind(params);
  const results = [];
  while (stmt.step()) results.push(stmt.getAsObject());
  stmt.free();
  return results;
}

function _getCourses() {
  return _queryAll(`
    SELECT c.course_id, c.title, c.teacher,
           COUNT(CASE WHEN l.summary IS NOT NULL THEN 1 END) AS summary_count,
           COUNT(l.sub_id) AS total_count,
           MAX(l.processed_at) AS last_updated
    FROM courses c
    LEFT JOIN lectures l ON c.course_id = l.course_id
    GROUP BY c.course_id
    ORDER BY last_updated DESC NULLS LAST
  `);
}

function _getLectures(courseId) {
  const rows = _queryAll(`
    SELECT sub_id, sub_title, summary, processed_at,
           error_stage, error_msg, summary_model, transcript
    FROM lectures WHERE course_id = ? ORDER BY sub_id ASC
  `, [courseId]);
  return rows.map((r) => {
    r.state = _deriveState(r);
    delete r.transcript;
    return r;
  });
}

function _getLecture(subId) {
  const rows = _queryAll(`
    SELECT l.*, c.title AS course_title, c.teacher
    FROM lectures l JOIN courses c ON l.course_id = c.course_id
    WHERE l.sub_id = ?
  `, [subId]);
  if (!rows.length) return null;
  rows[0].state = _deriveState(rows[0]);
  return rows[0];
}

function _searchSummaries(query) {
  if (!query?.trim()) return [];
  return _queryAll(`
    SELECT l.sub_id, l.sub_title, l.summary, l.course_id, c.title AS course_title
    FROM lectures l JOIN courses c ON l.course_id = c.course_id
    WHERE l.summary LIKE '%' || ? || '%' OR l.sub_title LIKE '%' || ? || '%'
    ORDER BY l.processed_at DESC LIMIT 50
  `, [query, query]);
}

function _updateSummary(subId, newSummary) {
  if (!_db) throw new Error("Database not loaded");
  _db.run("UPDATE lectures SET summary = ?, summary_model = 'manual-edit' WHERE sub_id = ?",
    [newSummary, subId]);
}

function _exportDB() {
  if (!_db) throw new Error("Database not loaded");
  return _db.export();
}

window.ICS.db = {
  initDB: _initDB,
  ensureSchema: _ensureSchema,
  getCourses: _getCourses,
  getLectures: _getLectures,
  getLecture: _getLecture,
  searchSummaries: _searchSummaries,
  updateSummary: _updateSummary,
  exportDB: _exportDB,
};
