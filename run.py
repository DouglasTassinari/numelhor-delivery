import os

from numelhor import create_app

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    if app.config["PROD"]:
        from waitress import serve
        serve(app, host=os.environ.get("HOST", "0.0.0.0"), port=port, threads=8)
    else:
        app.run(host="127.0.0.1", port=port, debug=False)
