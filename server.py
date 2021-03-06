import logging
import datetime, json, os, requests, urllib
from flask import (Flask, flash, redirect, render_template,
                   request, session)
from jinja2 import StrictUndefined
import config, rec, utils, api_utils, db_utils
from model import (Repo, User, Follower, Account,
                   Stargazer, Dislike,
                   Watcher, Contributor,
                   Language, RepoLanguage,
                   db, connect_to_db)

if not os.environ.get("CLIENT_ID"):
    import secrets2
    logging.debug(os.environ.get("CLIENT_ID"))

app = Flask(__name__)
app.secret_key = "|/tGf*uu`]oBkg498D7d"
# Don't let undefined variables fail silently.
# app.jinja_env.undefined = StrictUndefined


@app.route("/")
def main():
    if "user_id" not in session:
        return render_template("home.html")

    user = User.query.get(session["user_id"])
    compliment = requests.get("https://hello.sar.ai").text
    return render_template("home.html",
                           user=user,
                           compliment=compliment)


@app.route("/about")
def about():
    flash("Oops! Not available yet.")
    return redirect("/")


@app.route("/me")
def get_my_profile():
    if "user_id" not in session:
        return redirect("/")

    return get_user_profile(session["user_id"])


@app.route("/user", methods=["GET"])
def get_user():
    user_id = request.args.get("user_id")
    login = request.args.get("login")

    if user_id:
      user_id = int(user_id)

    return get_user_profile(user_id=user_id, login=login)


def get_user_profile(user_id="", login=""):
    user = None

    if user_id:
        user = User.query.get(user_id)
    elif login:
        user = User.query.filter_by(login=login).first()
        if not user:
            user = User.query.filter(User.login.ilike(login)).first()

    if not user:
        flash("Unable to find user (id:{}, login:{}).".format(user_id, login))
        return redirect("/")

    return render_template("user_info.html",
                           user=user,
                           repos=user.repos)


@app.route("/logout")
def logout():
    if "user_id" in session:
        del session["user_id"]
        flash("Logged out.")

    return redirect("/")


@app.route("/login", methods=["GET"])
def login():
    if "user_id" in session:
        return redirect("/")


    session["state"] = os.environ.get("STATE")
    #TODO: set this to random string when OAuth is working.
    payload = {"client_id": os.environ.get("CLIENT_ID"),
               "state": session["state"]}

    # Unless user includes a scope, default to read-only public data.
    user_scope = request.args.get("scope")
    if user_scope:
        payload["scope"] = config.OAUTH_SCOPE
        session["scope"] = config.OAUTH_SCOPE

    p = requests.Request("GET", 
                         config.GITHUB_AUTH_REQUEST_CODE_URL,
                         params=payload).prepare()
    # logging.debug(p.url)
    return redirect(p.url)


@app.route("/auth", methods=["GET"])
def auth():
    if "user_id" in session:
        return redirect("/")
 
    code = request.args.get("code")
    state = request.args.get("state")

    if not code or state != session.get("state"):
        flash("""Oops. We couldn't authorize your Github account; 
                 please try again.""")
        return redirect("/")

    payload = {"client_id": os.environ.get("CLIENT_ID"),
               "client_secret": os.environ.get("CLIENT_SECRET"),
               "code": code,
               "state": session["state"]}
    # Unless user includes a scope, default to read-only public data.
    if "scope" in session:
        payload["scope"] = config.OAUTH_SCOPE

    r = requests.post(config.GITHUB_AUTH_REQUEST_TOKEN_URL, params=payload)
    access_token =  urllib.parse.parse_qs(r.text).get("access_token")
    if not access_token:
        flash("""Oops. We couldn't authorize your Github account; 
                 please try again.""")
        return redirect("/")
    access_token = access_token[0]

    g = api_utils.get_auth_api(access_token)
    user = g.get_user()
    utils.add_user(user)
    db_utils.account_login(user, access_token)
    session["user_id"] = user.id
    session["access_token"] = access_token

    flash("Successfully authenticated {} with Github!".format(user.login))
    return redirect("/")


@app.route("/recs", methods=["GET"])
def get_repo_recs_react():
    if "user_id" not in session:
        return redirect("/")

    scope = session.get("scope")
    limit = int(request.args.get("count", config.DEFAULT_COUNT))
    # offset = limit * (-1 + int(request.args.get("page", 1)))
    page = int(request.args.get("page", 1))
    login = request.args.get("login")
    user_id = request.args.get("user_id")
    if user_id:
        user_id = int(user_id)

        # If user_id parameter is included but not in database, redirect.
        if not utils.is_user_in_db(user_id):
            flash("No user found with id {}.".format(user_id))
            return redirect("/") 
        logging.debug("Using user_id {} for recs.".format(user_id))
        

    # Login parameter takes precedence.
    if login:
        if User.query.filter_by(login=login).count() == 0:
            flash("No user found with login {}.".format(login))
            return redirect("/")
        user_id = User.query.filter_by(login=login).first().user_id
        logging.debug("Using login {} for user_id {} for recs."
              .format(login, user_id))
    elif not user_id:
        user_id = session["user_id"]
        logging.debug(f"{session['user_id']}: Using logged in user for recs.")

    return render_template("repo_recs.html",
                           user_id=user_id,
                           count=limit,
                           page=page,
                           scope=scope)


@app.route("/get_repo_recs", methods=["GET"])
def get_repo_recs_json():
    """Return JSON of repo recommendations for a user_id or login."""
    if "user_id" not in session:
        return redirect("/")

    login = request.args.get("login")
    user_id = request.args.get("user_id")
    limit = int(request.args.get("count", config.DEFAULT_COUNT))
    page = int(request.args.get("page", 1))
    offset = 2*limit * (-1 + page)
    code = request.args.get("code")

    if (code == session.get("code") and page == session.get("page")):
        logging.warning(f"{session['user_id']}: Code {code} and page {page} already requested. Ignoring request.")
        return json.dumps({"Status": 404,
                           "action": "get_repo_recs",
                           "message": f"Code {code} and page {page} already requested. Ignoring request."})

    session["code"] = code
    session["page"] = page

    if user_id:
        user_id = int(user_id)

        # If user_id parameter is included but not in database, redirect.
        if not utils.is_user_in_db(user_id):
            flash("No user found with id {}.".format(user_id))
            return redirect("/") 
        
    # Login parameter takes precedence.
    if login:
        if User.query.filter_by(login=login).count() == 0:
            flash("No user found with login {}.".format(login))
            return redirect("/")
        user_id = User.query.filter_by(login=login).first().user_id
    elif not user_id:
        user_id = session['user_id']

    logging.debug(f"{session['user_id']}: Fetching recs, page {page}, code {code}.")

    # Note start time to estimate time to complete process.
    times = [datetime.datetime.now()]

    # import pdb; pdb.set_trace()
    slice_end = offset + 2*limit
    logging.debug(f"{session['user_id']}: Slicing recs from {offset} to {slice_end}.")
    recs = rec.get_repo_suggestions(user_id)
    recs = recs[offset:slice_end]
    times.append(datetime.datetime.now())
    rec_delta = (times[1] - times[0]).total_seconds()
    logging.info(f"{user_id}: get_repo_suggestions: {rec_delta} seconds.")
    
    filtered_recs = db_utils.filter_stars_from_repo_ids(recs, user_id)
    times.append(datetime.datetime.now())
    filter_delta = (times[2] - times[1]).total_seconds()
    logging.info(f"{user_id}: filter_stars_from_repo_ids: {filter_delta} seconds.")

    repos_query = Repo.query.filter(Repo.repo_id.in_(filtered_recs),
                                    Repo.owner_id != user_id)
    repos = repos_query.all()
    times.append(datetime.datetime.now())
    query_delta = (times[3] - times[2]).total_seconds()
    logging.info(f"{user_id}: Repo query: {query_delta} seconds.")

    repos_json = db_utils.get_json_from_repos(repos[:limit])
    times.append(datetime.datetime.now())
    json_delta = (times[4] - times[3]).total_seconds()
    logging.info(f"{user_id}: get_json_from_repos: {json_delta} seconds.")
    return repos_json


@app.route("/add_star", methods=["POST"])
def add_star():
    if "user_id" not in session or "access_token" not in session:
        flash("Please log in with your GitHub account.")
        return redirect("/")

    data = request.get_json()
    repo_id = data["repo_id"]
    access_token = session["access_token"]
    g = api_utils.get_auth_api(access_token)
    repo = g.get_repo(repo_id)
    user = g.get_user()
    user.add_to_starred(repo)

    if not user.has_in_starred(repo):
        logging.warning(f"{session['user_id']}: Unable to star repo {repo.name} ({repo.id}).")
        flash("Unable to star this repo ({}). Please try again later."
              .format(repo.name))
        return json.dumps({"Status": 404,
                           "action": "add_star",
                           "repo_id": repo.id})

    db_utils.add_stargazer(repo_id, session["user_id"])
    logging.debug(f"{session['user_id']}: Successfully added a star for repo {repo.name} ({repo.id})")
    return json.dumps({"Status": 204,
                       "action": "add_star",
                       "repo_id": repo.id})


@app.route("/remove_star", methods=["POST"])
def remove_star():
    if "user_id" not in session or "access_token" not in session:
        flash("Please log in with your GitHub account.")
        return redirect("/")

    data = request.get_json()
    repo_id = data["repo_id"]
    access_token = session["access_token"]
    g = api_utils.get_auth_api(access_token)
    repo = g.get_repo(repo_id)
    user = g.get_user()
    user.remove_from_starred(repo)

    if user.has_in_starred(repo):
        logging.warning(f"{session['user_id']}: Unable to unstar repo {repo.name} ({repo.id}).")
        flash("Unable to unstar this repo ({}). Please try again later."
              .format(repo.name))
        return json.dumps({"Status": 404,
                           "action": "remove_star",
                           "repo_id": repo.id})

    db_utils.remove_stargazer(repo_id, session["user_id"])
    logging.debug(f"{session['user_id']}: Successfully unstarred repo {repo.name} ({repo.id}).")
    return json.dumps({"Status": 204,
                       "action": "remove_star",
                       "repo_id": repo.id})

@app.route("/check_star", methods=["POST"])
def check_star():
    if "user_id" not in session or "access_token" not in session:
        flash("Please log in with your GitHub account.")
        return redirect("/")

    repo_id = int(request.args.get("repo_id"))

    # Build Pygithub api object then get user & repo objects
    g = api_utils.get_auth_api(session["access_token"])
    user = g.get_user()
    repo = utils.get_repo_object_from_input(repo_id)

    # Check github api for if user starred repo.
    if user.has_in_starred(repo):
        return json.dumps({"Status": 204,
                           "action": "check_star",
                           "repo_id": repo.id})

    return json.dumps({"Status": 404,
                       "action": "check_star",
                       "repo_id": repo.id})


@app.route("/add_dislike", methods=["POST"])
def add_dislike():
    if "user_id" not in session or "access_token" not in session:
        flash("Please log in with your GitHub account.")
        return redirect("/")

    data = request.get_json()
    repo_id = data.get("repo_id")
    user_id = session["user_id"]
    
    db_utils.add_dislike(repo_id, user_id)

    logging.debug(f"{session['user_id']}: Successfully added a dislike for repo {repo_id}.")
    return json.dumps({"Status": 204,
                       "action": "add_dislike",
                       "repo_id": repo_id})


@app.route("/remove_dislike", methods=["POST"])
def remove_dislike():
    if "user_id" not in session or "access_token" not in session:
        flash("Please log in with your GitHub account.")
        return redirect("/")

    data = request.get_json()
    repo_id = data["repo_id"]
    user_id = session["user_id"]

    db_utils.remove_dislike(repo_id, user_id)

    logging.debug(f"{session['user_id']}: Successfully removed a dislike for repo {repo_id}.")
    return json.dumps({"Status": 204,
                       "action": "remove_dislike",
                       "repo_id": repo_id})


@app.route("/update_user", methods=["POST"])
def update_user():
    user_id = session.get("user_id")

    if not user_id:
        return json.dumps({"Status": 400,
                           "action": "update_user",
                           "message": "No user_id."})

    crawl_depth = 1
    # import pdb; pdb.set_trace()
    data = request.get_json()

    # Note start time to estimate time to complete process.
    times = [datetime.datetime.now()]

    if data.get("crawlFurther"):
        #TODO: implement dynamic crawl
        # E.g., fetch depth/breadth of crawl and increase
        # Or pass crawl_further to utils functions and evaluate within add_stars, etc.
        crawl_depth = 2
        logging.debug(f"{session['user_id']}: Crawling further.")

    user_id = int(user_id)
    message = None
    crawled_since = (datetime.datetime.now()
               - datetime.timedelta(days = config.REFRESH_UPDATE_USER_REPOS_DAYS))

    if not db_utils.is_last_crawled_user_repos_good(user_id, crawled_since):
        logging.debug(f"{session['user_id']}: Updating repos.")
        utils.update_user_repos(user_id, force_refresh=True)

        # Log time to complete update_user_repos:
        times.append(datetime.datetime.now())
        delta = (times[-1] - times[-2]).total_seconds()
        logging.info(f"{user_id}: update_user_repos: {delta} seconds.")

        message = "User updated."

    if not db_utils.is_last_crawled_in_user_good(user_id, crawl_depth, crawled_since):
        logging.debug(f"{session['user_id']}: Crawling user ({crawl_depth}).")
        utils.crawl_from_user_to_repos(user_id,
                                       num_layers_to_crawl=crawl_depth,
                                       force_refresh=False)

        # Log time to complete crawl_from_user_to_repos:
        times.append(datetime.datetime.now())
        delta = (times[-1] - times[-2]).total_seconds()
        logging.info(f"{user_id}: crawl_from_user_to_repos({crawl_depth}): {delta} seconds.")

        logging.debug(f"{session['user_id']}: User updated.")
        message = "User updated."

    if message:
        return json.dumps({"Status": 200,
                           "action": "update_user",
                           "message": "User updated."})

    logging.debug(f"{session['user_id']}: User is up-to-date.")
    return json.dumps({"Status": 200,
                       "action": "update_user",
                       "message": "User up-to-date."})


@app.route("/get_graph", methods=["GET"])
def get_graph():
    return json.dumps(db_utils.build_graph1())


if __name__ == "__main__":
    # import logging
    # logger = logging.getLogger('timelog')
    # logger.setLevel(logging.INFO)
    # # create file handler which logs even debug messages
    # fh = logging.FileHandler('timelog.log')
    # fh.setLevel(logging.DEBUG)
    # formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    # fh.setFormatter(formatter)
    # # add the handlers to the logger
    # logger.addHandler(fh)
    # logging.getLogger('').addHandler(fh)

    logging.basicConfig(filename='timelog.log',
                    level=logging.INFO,
                    format='%(asctime)s | %(filename)s | %(message)s')


    # logging.info("test")
    # import test_time
    # logger.info("test2")

    # We have to set debug=True here, since it has to be True at the
    # point that we invoke the DebugToolbarExtension
    # app.debug = True

    # make sure templates, etc. are not cached in debug mode
    # app.jinja_env.auto_reload = app.debug

    # Use the DebugToolbar
    # DebugToolbarExtension(app)

    connect_to_db(app)

    app.run(port=5000, threaded=True)
