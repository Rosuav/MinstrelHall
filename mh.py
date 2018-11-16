from __future__ import print_function
from flask import Flask, render_template, g, Markup, request, redirect
import mistune as md
import psycopg2
import subprocess
import os
import sys
import time
import random
import smtplib
import binascii
import functools
import traceback
import config # Local config variables and passwords, not in source control
import datasets # Anything big and constant that is in source control but not cluttering up the code
app = Flask(__name__)

# TODO: Port to Python 3, the latest psycopg2, and the like. Then take
# advantage of context managers, merge in the membershipaccess script, etc.
# (The psycopg2 previously in use didn't support context managers. No, I was
# NOT running this on Python 2.4!)

# Enable Unicode return values for all database queries
# This would be the default in Python 3, but in Python 2, we
# need to enable these two extensions.
# http://initd.org/psycopg/docs/usage.html#unicode-handling
import psycopg2.extensions
psycopg2.extensions.register_type(psycopg2.extensions.UNICODE)
psycopg2.extensions.register_type(psycopg2.extensions.UNICODEARRAY)

@app.template_filter()
def markdown(text):
	return Markup(md.markdown(text,escape=True))

def get_db():
	if not hasattr(g, 'pgsql'):
		# Pull in the actual connection string from a non-git-managed file
		g.pgsql = psycopg2.connect(config.db_connection_string)
	return g.pgsql

def log_to_tmp(func):
	"""As well as regular exception processing, log them to /tmp"""
	@functools.wraps(func)
	def wrapper(*a, **kw):
		try:
			return func(*a, **kw)
		except Exception as e:
			with open("/tmp/mh-exception.log", "a") as f:
				print("----------------------", file=f)
				print(time.ctime(), file=f)
				print(sys.version, file=f)
				traceback.print_exc(file=f)
				print("----------------------", file=f)
			raise
	return wrapper

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
	db.commit()
	if not cp: return Response('Campaign not found', 404)
	return render_template("campaign.html", cp=cp)

@app.route("/memb/<hash>")
def membership(hash):
	if not request.is_secure:
		return redirect("https://gideon.rosuav.com/memb/"+hash)
	db = get_db()
	cur = db.cursor()
	cur.execute("select email from membership where hash=%s", (hash,))
	emails = subprocess.Popen(["sudo", "list_members", "committee"], stdout=subprocess.PIPE).communicate()[0].split("\n")
	for row in cur:
		if row[0] in emails: break
	else:
		# Not found - wrong hash, or no longer subscribed
		return "<!doctype html><html><head><title>Invalid access code</title></head><body><h1>Invalid access code</h1></body></html>"
	# PyDrive isn't set up in the system Python, so we explicitly invoke a different Python to do the work for us.
	# CJA 20160516: This is no longer strictly necessary, but I haven't gotten around to merging yet.
	p = subprocess.Popen(["/usr/local/bin/python2.7","membaccess.py","html"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd="/home/gideon/MembershipAccess")
	return p.communicate()[0] # TODO: Make sure this can't block

@app.route("/memb")
@app.route("/committee")
@log_to_tmp
def membership_setup():
	if not request.is_secure:
		return redirect("https://gideon.rosuav.com/committee")
	db = get_db()
	emails = subprocess.Popen(["sudo", "list_members", "committee"], stdout=subprocess.PIPE).communicate()[0]
	if bytes is not str: emails = emails.decode("ascii") # Py2/Py3 compat
	emails = emails.split("\n")
	cur = db.cursor()
	cur.execute("create table if not exists membership (email varchar primary key,hash varchar not null unique)")
	cur.execute("select email from membership")
	for email in cur:
		try: emails.remove(email[0])
		except ValueError: pass
	try: emails.remove("")
	except ValueError: pass
	if emails:
		s=smtplib.SMTP("localhost")
		for email in emails:
			hash = binascii.hexlify(os.urandom(8))
			if bytes is not str: hash = hash.decode("ascii")
			cur.execute("insert into membership values (%s, %s)", (email, hash))
			s.sendmail("no-reply@gilbertandsullivan.org.au",[email],"""Content-Type: text/plain; charset="us-ascii"
From: no-reply@gilbertandsullivan.org.au
To: %s
Subject: Committee information access

Access to the G&S Society membership database and committee data is by
personalized links. Your link is:
https://gideon.rosuav.com/committee/%s
Please retain this email for your records.

Thanks!

Chris Angelico
Domainmaster, gilbertandsullivan.org.au
""" % (email, hash))
		db.commit()
		return "Sent emails to:<ul><li>"+"<li>".join(emails)+"</ul>"
	db.commit()
	return "No new emails to send."

@app.route("/committee/<hash>")
@log_to_tmp
def committee_info(hash):
	# TODO: Dedup.
	if not request.is_secure:
		return redirect("https://gideon.rosuav.com/committee/"+hash)
	db = get_db()
	cur = db.cursor()
	cur.execute("select email from membership where hash=%s", (hash,))
	emails = subprocess.Popen(["sudo", "list_members", "committee"], stdout=subprocess.PIPE).communicate()[0]
	if bytes is not str: emails = emails.decode("ascii") # Py2/Py3 compat
	emails = emails.split("\n")
	for row in cur:
		if row[0] in emails: break
	else:
		# Not found - wrong hash, or no longer subscribed
		return "<!doctype html><html><head><title>Invalid access code</title></head><body><h1>Invalid access code</h1></body></html>"
	cur.execute("select gdrivepwd from committee")
	passwd = cur.fetchall()[0][0]
	db.commit()
	return render_template("committee.html", passwd=passwd, hash=hash)

bingo_status = {None: 0}
@app.route("/bingo/<channel>")
@log_to_tmp
def bingo(channel):
	data = datasets.BINGO.get(channel.lower())
	if not data:
		return "Channel name not recognized - check the link", 404
	# Prune the bingo status dict if it's past noon in UTC
	# Yeah that's an odd boundary to use. Got a better one?
	today = (int(time.time()) - 86400//2) // 86400
	if bingo_status[None] != today:
		bingo_status.clear()
		bingo_status[None] = today
	user = request.args.get("user")
	if user and user in bingo_status:
		cards = bingo_status[user]
	else:
		cards = list(enumerate(data["cards"], 1))
		random.shuffle(cards)
		cards.insert(12, (0, data.get("freebie", "&nbsp;"))) # Always in the middle square - not randomized
		if user: bingo_status[user] = cards
	# Note that having more than 25 cards (24 before the freebie) is fine.
	# It means that not all cells will be shown to all players.
	return render_template("bingo.html", cards=cards)

if __name__ == "__main__":
	import logging
	logging.basicConfig(level=logging.DEBUG)
	app.run(host='0.0.0.0')
