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
	for cp in campaigns: cp[4]="Yes" if cp[4] else "No"
	db.commit()
	return render_template("main.html", campaigns=campaigns)

@app.route("/campaign/<int:id>")
def campaign(id):
	db = get_db()
	cur = db.cursor()
	cur.execute("select id,name,dm,room,looking,playingtime,description from campaigns where id=%s", (id,))
	cp = list(cur.fetchone())
	if not cp: return Response('Campaign not found', 404)
	cp[4]="Looking" if cp[4] else "Not currently looking"
	db.commit()
	return render_template("campaign.html", cp=cp)

if __name__ == "__main__":
	import logging
	logging.basicConfig(level=logging.DEBUG)
	app.run(host='0.0.0.0')
