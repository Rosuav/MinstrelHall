from flask import Flask, render_template, g, Markup
import mistune as md
import psycopg2
import subprocess
import config # Local config variables and passwords, not in source control
app = Flask(__name__)

@app.template_filter()
def markdown(text):
	return Markup(md.markdown(text,escape=True))

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

@app.route("/campaign/<int:id>")
def campaign(id):
	db = get_db()
	cur = db.cursor()
	cur.execute("select id,name,dm,room,looking,playingtime,description from campaigns where id=%s", (id,))
	cp = cur.fetchone()
	if not cp: return Response('Campaign not found', 404)
	db.commit()
	return render_template("campaign.html", cp=cp)

@app.route("/memb/<hash>")
def membership(hash):
	# TODO: Verify the hash against an email address and check it's still subscribed.
	if False:
		return "<!doctype html><html><head><title>Invalid access code</title></head><body><h1>Invalid access code</h1></body></html>"
	# PyDrive isn't set up in the system Python, so we explicitly invoke a different Python to do the work for us.
	p = subprocess.Popen(["/usr/local/bin/python2.7","/home/gideon/MembershipAccess/membaccess.py","html"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
	return p.communicate()[0] # TODO: Make sure this can't block

if __name__ == "__main__":
	import logging
	logging.basicConfig(level=logging.DEBUG)
	app.run(host='0.0.0.0')
