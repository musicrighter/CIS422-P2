import flask
from flask import render_template
from flask import request
from flask import url_for
from flask import jsonify
import uuid
import CONFIG
import json
import logging

# Date handling 
import arrow # Replacement for datetime, based on moment.js
import datetime # But we still need time
from dateutil import tz  # For interpreting local times

# OAuth2  - Google library implementation for convenience
from oauth2client import client
import httplib2   # used in oauth2 flow

# Google API for services 
from apiclient import discovery

# Mongo database
from pymongo import MongoClient
#from bson import ObjectId
#Establish our mongo database connection
try: 
    dbclient = MongoClient(CONFIG.MONGO_URL)
    db = dbclient.meetings
    collection = db.meetings

except:
    print("Failure opening database.  Is Mongo running? Correct password?")
    sys.exit(1)

###
# Globals
###
app = flask.Flask(__name__)

SCOPES = 'https://www.googleapis.com/auth/calendar.readonly'
CLIENT_SECRET_FILE = CONFIG.GOOGLE_LICENSE_KEY  ## You'll need this
APPLICATION_NAME = 'CIS 422 Project 2'

#############################
#
#  Pages (routed from URLs)
#
#############################

@app.route("/", methods=['GET', 'POST'])
@app.route("/index", methods=['GET', 'POST'])
def index():
    app.logger.debug("Entering index")
    flask.session.pop('begin_date', None)
    return render_template('index.html')


@app.route("/mainpage")
def mainpage():
    app.logger.debug("Entering mainpage")
    flask.session['meetingID'] = request.args.get('ID')

    queryResult = collection.find({ "meetingID":flask.session['meetingID'] }) 
    if queryResult.count() != 0:
        flask.session['already'] = True
        return render_template('error.html')

    if 'begin_date' not in flask.session:
        flask.session['proposer'] = True
        flask.session['participant'] = False
        flask.session['email'] = False
        flask.session['running'] = True
        init_session_values()

    if 'calendars' in flask.session:
        flask.session.pop('calendars', None)

    return render_template('mainpage.html')


@app.route("/finalize")
def finalize():
    app.logger.debug("Entering finalize")
    flask.session['meetingID'] = request.args.get('ID')

    queryResult = collection.find({ "meetingID":flask.session['meetingID'] }) 
    if queryResult.count() == 0:
        flask.session['already'] = False
        return render_template('error.html')

    return render_template('finalize.html')


@app.route('/findMeeting', methods=['POST'])
def find_meeting():
    queryResult = collection.find({ "meetingID":flask.session['meetingID'] }) 
    busyTimes = []
    if queryResult.count() != 0:
        start = queryResult[0]['begin']
        end = queryResult[0]['end']
        for document in queryResult:
            if document['type'] == "busyTime":
                entry = (arrow.get(document['begin']).to('local'), arrow.get(document['end']).to('local'))
                busyTimes.append(entry) 
        free_times(busyTimes, start, end)
    else:
        flask.seesion['errorMessage'] = "Error: Invalid ID" 
    return render_template('finalize.html')


@app.route('/delete', methods=['POST'])
def delete():
    app.logger.debug("Entering delete")
    collection.remove({ "meetingID":flask.session['meetingID'] })
    return flask.redirect(flask.url_for('index'))


@app.route("/choose")
def choose():
    app.logger.debug("Checking credentials for Google calendar access")
    credentials = valid_credentials()
    if not credentials:
      app.logger.debug("Redirecting to authorization")
      return flask.redirect(flask.url_for('oauth2callback'))

    global gcal_service
    gcal_service = get_gcal_service(credentials)
    app.logger.debug("Returned from get_gcal_service")
    flask.session['calendars'] = list_calendars(gcal_service)
    return render_template('mainpage.html')


@app.route("/email")
def email():
  flask.session['meetingID'] = request.args.get('ID')
  flask.session['proposer'] = False
  flask.session['email'] = False
  flask.session['participant'] = True
  flask.session['running'] = True
  app.logger.debug("Checking credentials for Google calendar access")
  credentials = valid_credentials()
  if not credentials:
    app.logger.debug("Redirecting to authorization")
    return flask.redirect(flask.url_for('oauth2callback'))

  global gcal_service
  gcal_service = get_gcal_service(credentials)
  app.logger.debug("Returned from get_gcal_service")
  flask.session['calendars'] = list_calendars(gcal_service)
  return render_template('mainpage.html')


####
#
#  Google calendar authorization:
#      Returns us to the main /choose screen after inserting
#      the calendar_service object in the session state.  May
#      redirect to OAuth server first, and may take multiple
#      trips through the oauth2 callback function.
#
#  Protocol for use ON EACH REQUEST: 
#     First, check for valid credentials
#     If we don't have valid credentials
#         Get credentials (jump to the oauth2 protocol)
#         (redirects back to /choose, this time with credentials)
#     If we do have valid credentials
#         Get the service object
#
#  The final result of successful authorization is a 'service'
#  object.  We use a 'service' object to actually retrieve data
#  from the Google services. Service objects are NOT serializable ---
#  we can't stash one in a cookie.  Instead, on each request we
#  get a fresh serivce object from our credentials, which are
#  serializable. 
#
#  Note that after authorization we always redirect to /choose;
#  If this is unsatisfactory, we'll need a session variable to use
#  as a 'continuation' or 'return address' to use instead. 
#
####

def valid_credentials():
    """
    Returns OAuth2 credentials if we have valid
    credentials in the session.  This is a 'truthy' value.
    Return None if we don't have credentials, or if they
    have expired or are otherwise invalid.  This is a 'falsy' value. 
    """
    if 'credentials' not in flask.session:
      return None

    credentials = client.OAuth2Credentials.from_json( flask.session['credentials'] )

    if (credentials.invalid or credentials.access_token_expired):
      return None
    return credentials


def get_gcal_service(credentials):
  """
  We need a Google calendar 'service' object to obtain
  list of calendars, busy times, etc.  This requires
  authorization. If authorization is already in effect,
  we'll just return with the authorization. Otherwise,
  control flow will be interrupted by authorization, and we'll
  end up redirected back to /choose *without a service object*.
  Then the second call will succeed without additional authorization.
  """
  app.logger.debug("Entering get_gcal_service")
  http_auth = credentials.authorize(httplib2.Http())
  service = discovery.build('calendar', 'v3', http=http_auth)
  app.logger.debug("Returning service")
  return service

@app.route('/oauth2callback')
def oauth2callback():
  """
  The 'flow' has this one place to call back to.  We'll enter here
  more than once as steps in the flow are completed, and need to keep
  track of how far we've gotten. The first time we'll do the first
  step, the second time we'll skip the first step and do the second,
  and so on.
  """
  app.logger.debug("Entering oauth2callback")
  flow =  client.flow_from_clientsecrets(
      CLIENT_SECRET_FILE,
      scope= SCOPES,
      redirect_uri=flask.url_for('oauth2callback', _external=True))
  ## Note we are *not* redirecting above.  We are noting *where*
  ## we will redirect to, which is this function. 
  
  ## The *second* time we enter here, it's a callback 
  ## with 'code' set in the URL parameter.  If we don't
  ## see that, it must be the first time through, so we
  ## need to do step 1. 
  app.logger.debug("Got flow")
  if 'code' not in flask.request.args:
    app.logger.debug("Code not in flask.request.args")
    auth_uri = flow.step1_get_authorize_url()
    return flask.redirect(auth_uri)
    ## This will redirect back here, but the second time through
    ## we'll have the 'code' parameter set
  else:
    ## It's the second time through ... we can tell because
    ## we got the 'code' argument in the URL.
    app.logger.debug("Code was in flask.request.args")
    auth_code = flask.request.args.get('code')
    credentials = flow.step2_exchange(auth_code)
    flask.session['credentials'] = credentials.to_json()
    ## Now I can build the service and execute the query,
    ## but for the moment I'll just log it and go back to
    ## the main screen
    app.logger.debug("Got credentials")
    return flask.redirect(flask.url_for('choose'))

#####
#
#  Option setting:  Buttons or forms that add some
#     information into session state.  Don't do the
#     computation here; use of the information might
#     depend on what other information we have.
#   Setting an option sends us back to the main display
#      page, where we may put the new information to use. 
#
#####

@app.route('/setrange', methods=['POST'])
def setrange():
    """
    User chose a date range with the bootstrap daterange
    widget.
    """
    app.logger.debug("Entering setrange")  
    daterange = request.form.get('daterange')
    flask.session['daterange'] = daterange
    daterange_parts = daterange.split()
    flask.session['begin_date'] = interpret_date(daterange_parts[0])
    flask.session['end_date'] = interpret_date(daterange_parts[2])
    flask.session['proposer'] = False

    record = { "type": "daterange", 
               "begin": flask.session['begin_date'],
               "end": flask.session['end_date'],
               "meetingID": flask.session['meetingID']
             }
    collection.insert(record)

    return flask.redirect(flask.url_for("choose"))

@app.route('/submit_times', methods=['POST'])
def submit_times():
    """
    Get the selected calendars from the mainpage page and 
    call busy_times with the list of calendars
    """
    flask.session['email'] = True
    flask.session['running'] = False

    app.logger.debug("Entering submit_times")
    checked_cals = request.form.getlist('calendar')
    all_cals = flask.session['calendars']
    cal_list = []
    for cal in all_cals:
        if cal['summary'] in checked_cals:
            cal_list.append(cal)

    busy_times(cal_list)
    meetingID = flask.session['meetingID']

    return render_template('mainpage.html', ID=meetingID)

####
#
#   Initialize session variables 
#
####

def init_session_values():
    """
    Start with some reasonable defaults for date and time ranges.
    Note this must be run in app context ... can't call from main. 
    """
    # Default date span = tomorrow to 1 week from now
    now = arrow.now('local')
    tomorrow = now.replace(days=+1)
    nextweek = now.replace(days=+7)
    flask.session["begin_date"] = tomorrow.floor('day').isoformat()
    flask.session["end_date"] = nextweek.ceil('day').isoformat()
    flask.session["daterange"] = "{} - {}".format(
        tomorrow.format("MM/DD/YYYY"),
        nextweek.format("MM/DD/YYYY"))
    # Default time span each day, 9 to 5
    flask.session["begin_time"] = interpret_time("9am")
    flask.session["end_time"] = interpret_time("5pm")


def interpret_time( text ):
    """
    Read time in a human-compatible format and
    interpret as ISO format with local timezone.
    May throw exception if time can't be interpreted. In that
    case it will also flash a message explaining accepted formats.
    """
    app.logger.debug("Decoding time '{}'".format(text))
    time_formats = ["ha", "h:mma",  "h:mm a", "H:mm"]
    try: 
        as_arrow = arrow.get(text, time_formats).replace(tzinfo=tz.tzlocal())
        app.logger.debug("Succeeded interpreting time")
    except:
        app.logger.debug("Failed to interpret time")
        flask.flash("Time '{}' didn't match accepted formats 13:30 or 1:30pm"
              .format(text))
        raise
    return as_arrow.isoformat()


def interpret_date( text ):
    """Convert text of date to ISO format used internally, with local time zone"""
    try:
      as_arrow = arrow.get(text, "MM/DD/YYYY").replace(
          tzinfo=tz.tzlocal())
    except:
        flask.flash("Date '{}' didn't fit expected format 12/31/2001")
        raise
    return as_arrow.isoformat()


def local_date(date):
    """Convert date to local format used internally, with local time zone"""
    try:
      as_arrow = arrow.get(date).to('local')
    except:
        flask.flash("Date '{}' didn't fit expected format")
        raise
    return as_arrow


def format_date(date):
    """Convert date to ddd MM/DD/YYYY"""
    try:
      as_arrow = arrow.get(date).format('ddd MM/DD/YYYY h:mm a')
    except:
        flask.flash("Date '{}' didn't fit expected format")
        raise
    return as_arrow


def next_day(isotext):
    """
    ISO date + 1 day (used in query to Google calendar)
    """
    as_arrow = arrow.get(isotext)
    return as_arrow.replace(days=+1).isoformat()

####
#
#  Functions (NOT pages) that return some information
#
####

def busy_times(cal_list):
    """
    Create a list of times from selected calendars of a google account that has 
    events which are blocking. These are added to the database under the specified
    meeting ID given in the first step
    """
    app.logger.debug("Entering busy_times")

    result = collection.find({ "type":"daterange", "meetingID":flask.session['meetingID'] })[0]
    ID = flask.session['meetingID']
    begin_date = result['begin']
    end_date = result['end']

    busyList = []

    if begin_date == end_date: 
        end_date = arrow.get(end_date).replace(days=+1).isoformat()

    for calendar in cal_list:
        calendarID = calendar['id']
        freebusy_query = {
          "timeMin" : begin_date,
          "timeMax" : end_date,
          "items" :[
            {
              "id" : calendarID
            }
          ]
        }
        credentials = valid_credentials()
        gcal_service = get_gcal_service(credentials)

        # people_resource = gcal_service.people()
        # people_document = people_resource.get(userId='me').execute()
        # name = people_document['id']

        queryResult = gcal_service.freebusy().query(body=freebusy_query)  
        busyRecords = queryResult.execute()
        busyTimes = busyRecords['calendars'][calendarID]['busy']

        if busyTimes:
            for pair in busyTimes:
                start = pair['start']
                end = pair['end']
                times = {
                "type": "busyTime",
                "begin": start,
                "end": end,
                "meetingID": ID
                }
                collection.insert(times)
    return
    

def free_times(busyTimes, startTime, endTime):
    """
    Create a list of free times created from the inverse of the previous busy times list. 
    Sorted by start time and formated according to the type of event. 
    """
    freeTimes = []
    startTime = arrow.get(startTime)
    endTime = arrow.get(endTime)
    allBusyTimes = addNights(busyTimes, startTime, endTime)
    sortedTimes = sorted(allBusyTimes, key=lambda times: times[0])
    unionizedTimes = fix_overlaps(sortedTimes)
    for i in range(len(unionizedTimes)):
        if i == 0:
            if startTime < startTime.replace(hour=9, minute=0):
                startTime = startTime.replace(hour=9, minute=0)
            if unionizedTimes[i][0] > unionizedTimes[i][0].replace(hour=17, minute=0):
                correctedTime = unionizedTimes[i][0].replace(hour=17, minute=0)
                beforeFirstEvent = (startTime, correctedTime)
            else:
                if startTime != unionizedTimes[i][0]:
                    beforeFirstEvent = (startTime, unionizedTimes[i][0])
                else:
                    beforeFirstEvent = "First Event is 9am"
            if beforeFirstEvent != "First Event is 9am":
                freeTimes.append(beforeFirstEvent)
        elif (i > 0) and (i < (len(unionizedTimes)-1)):
            if unionizedTimes[i-1][1].hour == 9:
                withOrWithoutAddedTime = unionizedTimes[i-1][1]
            else:
                withOrWithoutAddedTime = unionizedTimes[i-1][1]
            freeTime = (withOrWithoutAddedTime, unionizedTimes[i][0])
            freeTimes.append(freeTime)
        else:
            endTime = endTime.replace(hour=17, minute=0)
            if unionizedTimes[i-1][1] < unionizedTimes[i-1][1].replace(hour=9, minute=0):
                correctedTime = unionizedTime[i-1][1].replace(hour=9, minute=0)
                afterLastEvent = (correctedTime, endTime)
            else:
                afterLastEvent = (unionizedTimes[i-1][1], endTime)
            freeTimes.append(afterLastEvent)
    print_times(freeTimes)
    return freeTimes


def fix_overlaps(times):
    for i in range(len(times)-1):
        if i < (len(times)-1):
            if times[i][1] >= times[i+1][0]:
                if times[i][1] == times[i+1][0]:
                    newTuple = (times[i][0], times[i+1][1])
                if times[i][1] < times[i+1][1]:
                    newTuple = (times[i][0],times[i+1][1])
                else:
                    newTuple = times[i]
                del times[i+1]
                times[i] = newTuple
                i = i-1
    return times


def addNights(times, startTime, endTime):
    days = arrow.Arrow.span_range('day', startTime, endTime)
    for day in days:
        busyNightTime = (day[0].replace(hour=17, minute=0), day[1].replace(days=+1, hour=9, minute=0, second=0, microsecond=0))
        times.append(busyNightTime)
    return times 


def print_times(times_list):
    """
    Prints the times given by a list in an arrow "ddd MM/DD/YYYY h:mm a" format to the mainpage page.
    """
    app.logger.debug("Entering print_times")

    for time in times_list:
        finalBeginTime = format_date(time[0])
        finalEndTime = format_date(time[1])

        if arrow.get(time[0]).date() == arrow.get(time[1]).date():
          flask.flash("{} - {}".format(finalBeginTime, arrow.get(time[1]).format("h:mm a")))

        else:
          flask.flash("{} - {}".format(finalBeginTime, finalEndTime))
    return times_list

  
def list_calendars(service):
    """
    Given a google 'service' object, return a list of
    calendars.  Each calendar is represented by a dict, so that
    it can be stored in the session object and converted to
    json for cookies. The returned list is sorted to have
    the primary calendar first, and selected (that is, displayed in
    Google Calendars web app) calendars before unselected calendars.
    """
    app.logger.debug("Entering list_calendars")  
    calendar_list = service.calendarList().list().execute()["items"]
    result = [ ]
    for cal in calendar_list:
        kind = cal["kind"]
        id = cal["id"]
        if "description" in cal: 
            desc = cal["description"]
        else:
            desc = "(no description)"
        summary = cal["summary"]
        # Optional binary attributes with False as default
        selected = ("selected" in cal) and cal["selected"]
        primary = ("primary" in cal) and cal["primary"]
        

        result.append(
          { "kind": kind,
            "id": id,
            "summary": summary,
            "selected": selected,
            "primary": primary
            })
    return sorted(result, key=cal_sort_key)


def cal_sort_key( cal ):
    """
    Sort key for the list of calendars:  primary calendar first,
    then other selected calendars, then unselected calendars.
    (" " sorts before "X", and tuples are compared piecewise)
    """
    if cal["selected"]:
       selected_key = " "
    else:
       selected_key = "X"
    if cal["primary"]:
       primary_key = " "
    else:
       primary_key = "X"
    return (primary_key, selected_key, cal["summary"])


#################
#
# Functions used within the templates
#
#################

@app.template_filter( 'fmtdate' )
def format_arrow_date( date ):
    try: 
        normal = arrow.get( date )
        return normal.format("ddd MM/DD/YYYY")
    except:
        return "(bad date)"

@app.template_filter( 'fmttime' )
def format_arrow_time( time ):
    try:
        normal = arrow.get( time )
        return normal.format("HH:mm")
    except:
        return "(bad time)"
    
#############


if __name__ == "__main__":
  # App is created above so that it will
  # exist whether this is 'main' or not
  # (e.g., if we are running in a CGI script)

  app.secret_key = str(uuid.uuid4())  
  app.debug=CONFIG.DEBUG
  app.logger.setLevel(logging.DEBUG)
  # We run on localhost only if debugging,
  # otherwise accessible to world
  if CONFIG.DEBUG:
    # Reachable only from the same computer
    app.run(port=CONFIG.PORT)
  else:
    # Reachable from anywhere 
    app.run(port=CONFIG.PORT,host="0.0.0.0")
    
