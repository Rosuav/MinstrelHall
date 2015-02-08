from flask import Flask, render_template, g
import psycopg2
import config # Local config variables and passwords, not in source control
app = Flask(__name__)

def get_db():
	if not hasattr(g, 'pgsql'):
		# Pull in the actual connection string from a non-git-managed file
		g.pgsql = psycopg2.connect(config.db_connection_string)
	return g.pgsql

@app.route("/")
def mainpage():
	db = get_db()
	cur = db.cursor()
	cur.execute("select id,name,dm,room,looking,playingtime from campaigns where room!='dead'")
	campaigns = cur.fetchall()
	db.commit()
	return render_template("main.html", campaigns=campaigns)

if __name__ == "__main__":
	import logging
	logging.basicConfig(level=logging.DEBUG)
	app.run(host='0.0.0.0')
