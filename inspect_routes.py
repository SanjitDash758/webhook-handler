from app.main import app

routes = [
    (r.methods, r.path)
    for r in app.routes
    if hasattr(r, "methods") and r.methods
]

for methods, path in sorted(routes, key=lambda x: x[1]):
    methods_str = ",".join(sorted(methods))
    print(f"{methods_str:20s} {path}")