import neo4j
import streamlit as st
import pandas as pd
from neo4j import GraphDatabase, exceptions
from Spotify2DBScript import Neo4jHelper,API2DB,apiHelper
from datetime import datetime, timezone
import math
from streamlit.components.v1 import html

# initialize Neo4jHelper for making db queries, along w/ an apiHelper for spotify API calls
neo4jManager = Neo4jHelper()
apiManager = apiHelper()

def nav_to_auth_url(auth_url):
    nav_script = """
        <meta http-equiv="refresh" content="0; url='%s'">
    """ % (auth_url)
    st.write(nav_script, unsafe_allow_html=True)

###################################################### Report Queries #############################################################

# pulls Boolean indicator that indicates whether a refresh token is expired or not
def check_refresh_token_expired(Neo4jManager):
    query = f"""
        MATCH (n:Config)
        RETURN n.refresh_token_expired AS refreshTokenExpired
    """
    
    result = neo4jManager.getResultFromDB(query,params = {},output_values=["refreshTokenExpired"])
    return result["refreshTokenExpired"][0]

# pulls Boolean indicator that indicates whether a refresh token is expired or not
def store_refresh_token_expired(Neo4jManager,value):
    query = f"""
        MATCH (n:Config)
        SET n.refresh_token_expired = $value
        RETURN n AS output
    """

    result = neo4jManager.getResultFromDB(query,params = {"value":value},output_values=["output"])
    return result["output"][0]

# function gets the total number of nodes that exist for a specific node_type
def getTotalNodes(Neo4jManager, node_type):
    query = f"""
        MATCH (n:{node_type})
        RETURN count(n) AS totalCount
        """
    

    params = {
        'node_type':node_type
    }

    result = neo4jManager.getResultFromDB(query, params, output_values=["totalCount"])
    return result["totalCount"][0]



# function pulls the listens over a interval defined by 4 values (measure_type, start_day, end_day, and granularity). start_day should be a UTC timestamp in seconds. 
# Granularity determines how many datapoints will be returned. start_time dictates when values will start being produced. All values returned will start at start time, then move into the past based on measure_type and granularity.
# Note that this value will determine how many data points will be sent. Function returns a 2d pandas dataframe (time vs # plays)
# measure_type determines how the timeframes will be returned in the graphic. The options available are:
# 1. "Day": Graphic will show listens by day
# 2. "Hour": Graphic will show listens by the hour
# 3. "Week": Graphics Will show listens by the week
def getListensOverTime(Neo4jManager,start_time,measure_type,granularity = 10,genre_name = None):
# create pandas dataframe
    data = {
        'Time': [],
        'Listens':[]
    }

    # check to make sure a valide measure type was returned
    if(measure_type != "Day" and measure_type != "Hour" and measure_type != "Week"):
        print("Error: measure_type does not have a valid entry. Please check your getListensOverTime function for spelling mistakes")
        return

    # Run query to pull a count of all the values in "play_history" that are between start_time and end_time for all tracks
    for i in range(granularity):

        # set previous start time to be the end time
        end_time = start_time

        # set a new start time based on measure_type
        if(measure_type == "Day"):
            # move back by a day of time
            start_time = start_time - 86400

        elif(measure_type == "Hour"):
            # move back by an hour of time
            start_time = start_time - 3600
        
        elif(measure_type == "Week"):
            # move back by a week of time
            start_time = start_time - 604800

        # if genre name isn't specified. Search across all genres
        if genre_name is None:
            query = f"""
                MATCH (n:Track)
                WITH n, [value IN n.play_history WHERE $start_time <= value <= $end_time] AS matchingValues
                RETURN sum(size(matchingValues)) AS totalCount
            """

            params = {
                'start_time': start_time,
                'end_time': end_time
            }
        else:
            # good example of how path approach of Graph DB is useful and quick
            query = """
                MATCH (n:Track)
                WHERE EXISTS {
                    MATCH (n)-[IN_ALBUM]->(album)-[GENRE]->(genre)
                    WHERE genre.name = $genre_name
                }
                WITH n, [value IN n.play_history WHERE $start_time <= value <= $end_time] AS matchingValues
                RETURN sum(size(matchingValues)) AS totalCount
            """

            params = {
                'start_time': start_time,
                'end_time': end_time,
                'genre_name': genre_name
            }

        result = Neo4jManager.runQuery(query=query, params=params)

        utc_datetime = datetime.fromtimestamp(end_time, tz=timezone.utc)
        local_datetime = utc_datetime.astimezone()

        if(measure_type == "Day" or measure_type == "Week"):
            x = local_datetime.strftime('%b %d')

        elif(measure_type == "Hour"):
            x = f"{local_datetime:%A}, {local_datetime.hour % 12 or 12}{local_datetime:%p}"

        result = neo4jManager.getResultFromDB(query, params, "totalCount")         

        data['Time'].append(x)
        data['Listens'].append(result["totalCount"][0])

        df = pd.DataFrame(data)

    return df

# function pulls the most popular names of a certain node type (set with the variable node_type). 
# This can be set to Track, Playlist, Genre, or Artist. Use the variable "number_of_entries" to set how many names are returned (default is 5, max is 100)
# the function returns a list of names.
def getFavorites(Neo4jManager, node_type, number_of_entries = 5):  

    # error check to ensure num_of_entries isn't greater than maximum     
    if number_of_entries > 100:
        print("Error: num of entries to pull is too high")
    else:
        data = {
            "name":[],
            "listens":[]
        }

         # Query returns the name of the i'th highest number of times played for selected node type
        query = f"""
            MATCH (n: {node_type})
            WHERE n.play_history IS NOT NULL
            WITH n
            ORDER BY size(n.play_history) DESC
            RETURN n.name AS name,size(n.play_history) AS listens
            LIMIT {number_of_entries}
        """

        params = {"node_type":node_type, "num_of_entries":number_of_entries}
        result = neo4jManager.getResultFromDB(query,params,["name","listens"])

        for i in range(number_of_entries):
            data["name"].append(result["name"][i])
            data["listens"].append(result["listens"][i])

        df = pd.DataFrame(data)    
        return df

# calculates the "obscurity score" for a user, which is a percentage that articulates how many unique artists, tracks, and albums they listen to. This is based off the popularity metric.
def calculateObscurityScore(Neo4jManager):
    query = f"""
        MATCH(n:Track|Album)
        WHERE n.popularity IS NOT NULL
        RETURN round(avg(n.popularity)) AS obscurityScore
    """
    result = neo4jManager.getResultFromDB(query, params={}, output_values=["obscurityScore"])
    return int(result["obscurityScore"][0])


def calculateDiversityScore(Neo4jManager):
    # use a log function to flatten, and normalize with a constant to keep between 0 and 1
    return int((math.log((getTotalNodes(Neo4jManager, "Artist") + 1),50)/1.6)*100)

# function gets the tracks that were played in a time period (a timestamp in seconds) defined by "loopback_time"
def getRecentlyPlayed(Neo4jManager,node_type,current_time,lookback_time):
    start_time = current_time - lookback_time
    print(f"start_time: {start_time}")
    print(f"end_time: {current_time}")
    query = f"""
        MATCH (n:{node_type})
        WITH n, [value IN n.play_history WHERE value >= {start_time} AND value <= {current_time}] AS matchingValues
        WHERE size(matchingValues) > 0  
        RETURN n.name AS recentlyPlayed
    """

    params = {
        'node_type': node_type,
        'start_time': start_time,
        'end_time': current_time
    }

    result = neo4jManager.getResultFromDB(query, params, output_values=["recentlyPlayed"])
    return result["recentlyPlayed"]

# function will create a pandas dataset that has 4 times of day (Morning, Afternoon, Evening, and Night), 
# each day will show the number of listens over a 90 day period for each period of the day
def getTimeOfDay(node_type):
    data = {
        'Time': ["Morning","Afternoon","Evening","Night"],
        'Listens':[]
    }

    # dictionary defines the ranges for each time of day
    timesOfDay = {
        "Morning":(1,12),
        "Afternoon":(13,16),
        "Evening":(17,19),
        "Night":(20,24)
    }

    for i in timesOfDay.keys():
        query = f"""
            MATCH (n:{node_type})
                WITH n, [value IN n.hour_of_day WHERE {timesOfDay[i][0]} <= value <= {timesOfDay[i][1]}] AS matchingValues
                RETURN sum(size(matchingValues)) AS totalCount
        """

        result = neo4jManager.getResultFromDB(query, params={},output_values=["totalCount"])

        data['Listens'].append(result["totalCount"][0])
    
    df = pd.DataFrame(data)

    return df

########################################################### Main ##################################################################
def main():
    # Load the CSS file
    cssFile = "style.css"
    with open(cssFile) as f:
        st.markdown(f'<style>{f.read()}</style>', unsafe_allow_html=True)

    # Get the current UTC time
    current_utc_time = datetime.now(timezone.utc)

    # Convert to a UNIX timestamp
    utc_timestamp = current_utc_time.timestamp()

    print("Checking to see if logged in...")

    # check to see if refresh token is valid w/o authorizing to determin which screen user sees
    isLoggedIn = neo4jManager.checkRefreshToken(apiManager=apiManager,utc_timestamp=utc_timestamp)

    # pull value that indicates whether refresh token is expired
    refresh_token_expired = check_refresh_token_expired(neo4jManager)

    # if refresh token is expired or doesn't exist (see checkRefreshToken in Spotify2DBScript.py for reference),
    # then change to login screen and remove cached variables associated with auth
    if(isLoggedIn == False and refresh_token_expired == True):
        st.session_state['page_state'] = 0
    else:
        st.session_state['page_state'] = 1

    # if refresh token doesn't exist in db or is expired, create login button that will initiate oauth flow
    if(st.session_state['page_state'] == 0):
        print("Within Login Session")

        # if button is pressed, generate auth url, store state and verifier in DB, and redirect user to spotify auth page
        # need to store in DB and not using caching is because when redirected back, a new streamlink session is started, and cache 
        # is reset..
        with st.container():
            # Create a layout with three columns
            col1, col2, col3 = st.columns([1, 1, 1])

            with col1:
                st.image("Images/logo.png", use_container_width=True)
            
            with col3:
                if st.button("Login To Spotify"):
                    # Generate auth url to be used
                    auth_url,code_verifier,state = apiHelper.getAuthCodeURL()
                
                    params = {
                        'state':state,
                        'code_verifier':code_verifier
                    }

                    query = """
                        MATCH (n:Config {name:"configuration"})
                        SET n.state = $state, n.code_verifier = $code_verifier
                        RETURN n
                        """
                    
                    result = neo4jManager.runQuery(query=query,params=params)

                    print("generated Auth URL..")
                    print("Login Button Pressed.. Running Oauth Flow..")
                    nav_to_auth_url(auth_url)

                    # Wait for user to authorize scope by checking if url has auth code param inside it
                    i = False
                    while 'code' not in st.query_params:
                        if(i == False):
                            i=True
                            print("Waiting For User To Auth")
                        #print(f"query params before webpage is opened: {st.query_params}")
        
        # If we are being redirected back from Authorization page, then pull the auth code, use it to get a refresh token, and store in DB        
        if 'code' and 'state' in st.query_params:
            st.empty()
            with st.spinner("Pulling Spotify Stats..."):
                # pull state and code_verifier from DB
                query = """
                        MATCH (n:Config) RETURN n.state AS state, n.code_verifier AS code_verifier
                    """

                result = neo4jManager.runQuery(query=query)
                state = result.iloc[0]["state"]
                code_verifier = result.iloc[0]["code_verifier"]

                if(st.query_params['state'] == state):
                    
                    # get a new refresh token and access token, and store in DB
                    print("getting access token to sync\n")
                    access_token, refresh_token = apiManager.getRefreshToken(Neo4jManager=neo4jManager,auth_code=st.query_params['code'],code_verifier=code_verifier)

                    #if access token isn't able to get pulled, throw an error and store metric in DB to tell front end that refresh token is expired
                    if access_token is None:
                        st.error('Error: access token was unable to be pulled.. this likely means the refresh token is expired. Rerun so user can reauthorize', icon=":material/sentiment_dissatisfied:")
                        store_refresh_token_expired(neo4jManager,True)
                        st.rerun()
                    else:
                        store_refresh_token_expired(neo4jManager,False)
                
                    API2DB(access_token = access_token,refresh_token=refresh_token)
                    st.session_state['page_state'] = 1
                    st.query_params.clear()
                    st.balloons()
                    st.rerun()
                else:
                    print("Error: State mismatch in API Call. Could Be a Potential CSRF Attack")
                    st.session_state['page_state'] = 2
                    st.session_state['error_message'] = "Error: State mismatch in API Call. Could Be a Potential CSRF Attack" 
        else:
            st.title("Spotify Stats Page")
            st.write("This application showcases statistics about your Spotify listens. Simply login to your Spotify account by selecting 'Login to Spotify' on the top right, approve the API scope, and the stats will automatically be generated.")
  
                
                
        
    elif(st.session_state['page_state'] == 1):
        with st.container():
            # Create a layout with three columns
            col1, col2, col3 = st.columns([1, 1, 1])

            with col1:
                st.image("Images/logo.png", use_container_width=True)
            
            with col3:

                #if user selects to logout, remove refresh token from DB
                if(st.button("Logout")):
                    query = """
                        MATCH(n:Config)
                        SET n.refresh_token=""
                        RETURN n
                    """
                    neo4jManager.runQuery(query)
            
        st.title("Spotify Stats")

        #pull any recent songs from Spotify and put into DB
        with st.spinner("Loading Spotify Stats..."):
            API2DB(utc_timestamp = utc_timestamp)
        
    else:
        st.write(st.session_state["error_message"])

if __name__ == "__main__":
    main()
