from __future__ import print_function
from flask import Flask, render_template, g, Markup, request, redirect
from flask_sockets import Sockets
import mistune as md
import psycopg2
import subprocess
import os
import sys
import json
import time
import random
import smtplib
import binascii
import functools
import traceback
import collections
import config # Local config variables and passwords, not in source control (see config_sample.py)
import datasets # Anything big and constant that is in source control but not cluttering up the code
app = Flask(__name__)
sockets = Sockets(app)

# TODO: Port to Python 3, the latest psycopg2, and the like. Then take
# advantage of context managers, merge in the membershipaccess script, etc.
# (The psycopg2 previously in use didn't support context managers. No, I was
# NOT running this on Python 2.4!)

@app.template_filter()
def markdown(text):
	return Markup(md.markdown(text))

def get_db():
	if not hasattr(g, 'pgsql'):
		# Pull in the actual connection string from a non-git-managed file
		g.pgsql = psycopg2.connect(config.db_connection_string)
	return g.pgsql

def log_to_tmp(func):
	"""As well as regular exception processing, log them to /tmp and stderr"""
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
			traceback.print_exc(file=sys.stderr)
			raise
	return wrapper

@app.route("/")
def mainpage():
	db = get_db()
	with db, db.cursor() as cur:
		cur.execute("select id,name,dm,room,looking,playingtime from campaigns where room!='dead'")
		campaigns = cur.fetchall()
	return render_template("main.html", campaigns=campaigns)

@app.route("/campaign/<int:id>")
def campaign(id):
	db = get_db()
	with db, db.cursor() as cur:
		cur.execute("select id,name,dm,room,looking,playingtime,description from campaigns where id=%s", (id,))
		cp = cur.fetchone()
	if not cp: return Response('Campaign not found', 404)
	return render_template("campaign.html", cp=cp)

@app.route("/memb/<hash>")
def membership(hash):
	#if not request.is_secure: # TODO: Detect if the pre-proxy request was via HTTPS
	#	return redirect("https://gideon.rosuav.com/committee")
	db = get_db()
	with db, db.cursor() as cur:
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
	#if not request.is_secure: # TODO: Detect if the pre-proxy request was via HTTPS
	#	return redirect("https://gideon.rosuav.com/committee")
	db = get_db()
	emails = subprocess.Popen(["sudo", "list_members", "committee"], stdout=subprocess.PIPE).communicate()[0]
	emails = emails.decode("ascii").split("\n")
	with db, db.cursor() as cur:
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
				hash = binascii.hexlify(os.urandom(8)).decode("ascii")
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
			return "Sent emails to:<ul><li>"+"<li>".join(emails)+"</ul>"
	return "No new emails to send."

@app.route("/committee/<hash>")
@log_to_tmp
def committee_info(hash):
	# TODO: Dedup.
	#if not request.is_secure: # TODO: Detect if the pre-proxy request was via HTTPS
	#	return redirect("https://gideon.rosuav.com/committee")
	db = get_db()
	with db, db.cursor() as cur:
		cur.execute("select email from membership where hash=%s", (hash,))
		emails = subprocess.Popen(["sudo", "list_members", "committee"], stdout=subprocess.PIPE).communicate()[0]
		emails = emails.decode("ascii").split("\n")
		for row in cur:
			if row[0] in emails: break
		else:
			# Not found - wrong hash, or no longer subscribed
			return "<!doctype html><html><head><title>Invalid access code</title></head><body><h1>Invalid access code</h1></body></html>"
		cur.execute("select gdrivepwd from committee")
		passwd = cur.fetchall()[0][0]
	return render_template("committee.html", passwd=passwd, hash=hash)

# TODO: Have a /bingo route that gives a nice list of available channels

bingo_status = collections.defaultdict(lambda: {None: {"all_sockets": set(), "scores": [[], [], [], [], []]}}, {None: 0})
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
	status = bingo_status[channel]
	user = request.args.get("user")
	if user and user in status:
		cards = status[user]["cards"]
	else:
		cards = list(enumerate(data["cards"], 1))
		if user != "noshuf": random.shuffle(cards) # Hack: Use the name "noshuf" for stable testing
		cards.insert(12, (0, data.get("freebie", "&nbsp;"))) # Always in the middle square - not randomized
		if user:
			status[user] = {"cards": cards, "marked": [True] + [False] * (len(cards)-1), "sockets": set()}
			baseline = status[None]["scores"][0]
			if user not in baseline: baseline.append(user)
	# Note that having more than 25 cards (24 before the freebie) is fine.
	# It means that not all cells will be shown to all players.
	return render_template("bingo.html",
		channel=channel, displayname=data["displayname"],
		cards=cards, user=json.dumps(user)
	)

@sockets.route("/bingo-live")
@log_to_tmp
def bingo_socket(ws):
	user = channel = today = None
	while not ws.closed:
		message = ws.receive()
		if message is None: break # ?? I think this happens on disconnection?
		try:
			msg = json.loads(message)
		except ValueError:
			continue # Ignore unparseable messages
		if today and bingo_status[None] != today:
			# All cards have been reset. Refresh the page.
			ws.send(json.dumps({"type": "refresh"}))
			break
		t = msg.get("type")
		if t == "init":
			if channel: continue # Already initialized
			c = msg.get("channel")
			if c not in datasets.BINGO: continue # Wrong channel name (shouldn't normally happen)
			channel = bingo_status[c]; today = bingo_status[None]
			channel[None]["all_sockets"].add(ws)
			user = msg.get("user")
			if not user:
				# If not logged in, just give the scores, nothing else
				ws.send(json.dumps({"type": "scores", "scores": channel[None]["scores"]}))
				continue
			if user not in channel:
				# Probably refreshed but got it from cache. The cards need to be rerandomized.
				ws.send(json.dumps({"type": "refresh"}))
				break
			channel[user]["sockets"].add(ws)
			ws.send(json.dumps({"type": "reset", "marked": channel[user]["marked"], "scores": channel[None]["scores"]}))
			continue
		if t == "mark":
			try:
				channel[user]["marked"][msg["id"]] = status = bool(msg["status"])
			except KeyError:
				# malformed message or not logged in, ignore it
				continue
			u = channel[user]
			# Calculate the best score. For now, don't bother highlighting where that is.
			best = 1 # Since the freebie starts out selected, you can never do worse than 1/5
			for indices in datasets.BINGO_INDICES:
				score = sum(u["marked"][u["cards"][i][0]] for i in indices)
				if score > best: best = score
			sc = channel[None]["scores"]
			# Add this person to any leaderboard now qualified
			for score, users in enumerate(sc[:best]):
				if user not in users:
					users.append(user)
			# Remove from any leaderboard no longer qualified
			for users in sc[best:]:
				try: users.remove(user)
				except ValueError: pass
			# Notify all other clients
			for sock in u["sockets"]:
				if sock is not ws:
					try:
						sock.send(json.dumps({"type": "mark", "id": msg["id"], "status": status}))
					except WebSocketError:
						pass # Probably a dead socket. It'll get reaped soon.
			# Send high score status to ALL sockets
			for sock in channel[None]["all_sockets"]:
				try:
					sock.send(json.dumps({"type": "scores", "scores": sc}))
				except WebSocketError:
					pass # Ditto
			continue
		# Otherwise it's an unknown message. Ignore it.
	if channel:
		if user and user in channel:
			channel[user]["sockets"].discard(ws)
		channel[None]["all_sockets"].discard(ws)

if __name__ == "__main__":
	import logging
	logging.basicConfig(level=logging.DEBUG)
	app.run(host='0.0.0.0')
