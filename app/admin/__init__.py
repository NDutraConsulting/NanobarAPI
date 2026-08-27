"""Everything behind `/admin/*`'s session gate -- two fully independent admin surfaces
(`admin.app`, the blog/booking admin; `admin.nanobar`, this project's own regression-brick/
observability admin), each with its own `SessionBackend`/`SQLiteAdminUserStore` (own SQLite
file), its own login page, and its own path-scoped session/CSRF cookies. Nothing is shared at
this package level -- see each subpackage's own `auth_db.py`/`login_routes.py`.
"""
