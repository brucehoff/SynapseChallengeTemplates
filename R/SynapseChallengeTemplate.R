#
#
# Executable template for Challenge scoring application
#
# To use this script, first install the Synapse R Client
# https://sagebionetworks.jira.com/wiki/display/SYNR/How+to+install+the+Synapse+R+Client
# Log in once using your user name and password
# 	synapseLogin(<username>, <password>)
# Your credentials will be saved after which you may run this script with no credentials.
# 
# Author: brucehoff
#
###############################################################################

library(RCurl)
library(RJSONIO)
library(synapseClient)

synapseLogin()

# if true, then tear down at the end, leaving the system in its initial state
# if false, leave the created objects in place for subsequent use
TEAR_DOWN_AFTER <- TRUE

# if 'TEAR_DOWN_AFTER' is set to false, then use unique names for projects and the evaluation:
CHALLENGE_PROJECT_NAME <- "SynapseChallengeTemplate R edition"
CHALLENGE_EVALUATION_NAME <- "SynapseChallengeTemplate R edition"
PARTICIPANT_PROJECT_NAME <- "SynapseChallengeTemplate Participant R edition"

# the page size can be bigger, we do this just to demonstrate pagination
PAGE_SIZE <- 20
# the batch size can be bigger, we do this just to demonstrate batching
BATCH_SIZE <- 20
# make sure there are multiple batches to handle
NUM_OF_SUBMISSIONS_TO_CREATE <- 2*PAGE_SIZE+1

WAIT_FOR_QUERY_ANNOTATIONS_SEC <- 20L # must be under a minute


findProject<-function(name) {
  queryResults<-synQuery(sprintf("select id from entity where \"name\"==\"%s\"", name))
  if (is.null(queryResults)) {
    NULL
  } else {
    if (dim(queryResults)[1]!=1) stop(sprintf("one row expected but %s found.", dim(queryResults)[1]))
    synGet(queryResults[1,1])
  }
}

findEvaluation<-function(name) {
  tryCatch(
    {
      result<-synRestGET(sprintf("/evaluation/name/%s", curlEscape(name)))
      synGetEvaluation(result$id)
    },
    # if not found a 404 status is returned, causing an error
    error = function(e) return(NULL)
  )
}

tearDown<-function() {
  # delete the participant's project
  project<-findProject(PARTICIPANT_PROJECT_NAME)
  if (!is.null(project)) {
    synDelete(project)
    message(sprintf("Deleted %s %s", propertyValue(project, "id"), propertyValue(project, "name")))
  }
  # delete the challenge evaluation queue along with the Submissions
  evaluation<-findEvaluation(CHALLENGE_EVALUATION_NAME)
  if (!is.null(evaluation)) {
    total<-1e+10
    while (total>0) {
      submissions<-synGetSubmissions(evaluationId=evaluation$id, limit=PAGE_SIZE, offset=0)
      total<-submissions@totalNumberOfResults
      for (submission in submissions@results) {
        synDelete(submission)
      }
    }
    synDelete(evaluation)
    message(sprintf("Deleted Evaluation queue %s %s", evaluation$id, evaluation$name))
  }
  # delete the participant's project
  project<-findProject(CHALLENGE_PROJECT_NAME)
  if (!is.null(project)) {
    synDelete(project)
    message(sprintf("Deleted %s %s", propertyValue(project, "id"), propertyValue(project, "name")))
  }
}

setGlobal<-function(name, value) {
  assign(name, value, envir = globalenv())
}

DEFAULT_FILE_CONTENT<-"some file content"

createFile<-function(content, filePath) {
  if (missing(content)) content<-DEFAULT_FILE_CONTENT
  if (missing(filePath)) filePath<- tempfile()
  connection<-file(filePath)
  writeChar(content, connection, eos=NULL)
  close(connection)  
  filePath
}

setUp<-function() {
  tearDown()
  
  # Create the Challenge Project
  challengeProject<-Project(name=CHALLENGE_PROJECT_NAME)
  challengeProject<-synStore(challengeProject)
  message(sprintf("Created project %s %s", propertyValue(challengeProject, "id"), propertyValue(challengeProject, "name")))
  
  # Create the Evaluation
  evaluation<-Evaluation(
    name=CHALLENGE_EVALUATION_NAME, 
    contentSource=propertyValue(challengeProject, "id"), 
    status="OPEN", 
    submissionInstructionsMessage="To submit to the XYZ Challenge, send a tab-delimited file as described here: https://...", 
    submissionReceiptMessage="Your submission has been received.   For further information, consult the leader board at https://...")
  evaluation<-synStore(evaluation)
  message(sprintf("Created Evaluation %s %s", evaluation$id, evaluation$name))
  
  # Create the participant project
  participantProject<-Project(name=PARTICIPANT_PROJECT_NAME)
  participantProject<-synStore(participantProject)
  message(sprintf("Created project %s %s", propertyValue(participantProject, "id"), propertyValue(participantProject, "name")))
  
  # Create a File to be used for Submission
  filePath<-createFile()
  participantFile<-File(path=filePath, parentId=propertyValue(participantProject, "id"))
  participantFile<-synStore(participantFile)
  
  list(challengeProject=challengeProject, 
    evaluation=evaluation, 
    participantProject=participantProject, 
    participantFile=participantFile)
}

submitToChallenge<-function(evaluation, participantFile) {
  CONTENT_TYPE <- "text/plain;charset=UTF-8"
  for (i in 1:NUM_OF_SUBMISSIONS_TO_CREATE) {
    submit(evaluation=evaluation, entity=participantFile, silent=TRUE)
  }
  message(sprintf("Submitted %s submissions to Evaluation queue %s", NUM_OF_SUBMISSIONS_TO_CREATE, evaluation$name))
}

validate<-function(evaluation) {
  total<-1e+10
  offset<-0
  statusesToUpdate<-list()
  while(offset<total) {
    submissionBundles<-synRestGET(sprintf("/evaluation/%s/submission/bundle/all?limit=%s&offset=%s&status=%s",
        evaluation$id, PAGE_SIZE, offset, "RECEIVED")) 
    total<-submissionBundles$totalNumberOfResults
    offset<-offset+PAGE_SIZE
    page<-submissionBundles$results
    if (length(page)>0) {
      for (i in 1:length(page)) {
        # need to download the file
        submission<-synGetSubmission(page[[i]]$submission$id)
        filePath<-getFileLocation(submission)
        # challenge-specific validation of the downloaded file goes here
        isValid<-TRUE
        if (isValid) {
          newStatus<-"VALIDATED"
        } else {
          newStatus<-"INVALID"
          sendMessage(list(), "Submission Acknowledgment", "Your submission is invalid. Please try again.")
        }
        subStatus<-page[[i]]$submissionStatus
        subStatus$status<-newStatus
        statusesToUpdate[[length(statusesToUpdate)+1]]<-subStatus
      }
    }
  }
  updateSubmissionStatusBatch(evaluation, statusesToUpdate)
}

BATCH_UPLOAD_RETRY_COUNT<-3

updateSubmissionStatusBatch<-function(evaluation, statusesToUpdate) {
  for (retry in 1:BATCH_UPLOAD_RETRY_COUNT) {
    tryCatch(
      {
        batchToken<-NULL
        offset<-0
        while (offset<length(statusesToUpdate)) {
          batch<-statusesToUpdate[(offset+1):min(offset+BATCH_SIZE, length(statusesToUpdate))]
          updateBatch<-list(
            statuses=batch, 
            isFirstBatch=(offset==0), 
            isLastBatch=(offset+BATCH_SIZE>=length(statusesToUpdate)),
            batchToken=batchToken
          )
          response<-synRestPUT(sprintf("/evaluation/%s/statusBatch",evaluation$id), updateBatch)
          batchToken<-response$nextUploadToken
          offset<-offset+BATCH_SIZE
        } # end while offset loop
        break
      }, 
      error=function(e){
        # on 412 ConflictingUpdateException we want to retry
        if (regexpr("412", e, fixed=TRUE)>0) {
          # will retry
        } else {
          stop(e)
        }
      }
    )
    if (retry<BATCH_UPLOAD_RETRY_COUNT) message("Encountered 412 error, will retry batch upload.")
  }
}

score<-function(evaluation) {
  total<-1e+10
  offset<-0
  statusesToUpdate<-list()
  while(offset<total) {
    if (TRUE) {
      # get ALL the submissions in the Evaluation
      submissionBundles<-synRestGET(sprintf("/evaluation/%s/submission/bundle/all?limit=%s&offset=%s",
          evaluation$id, PAGE_SIZE, offset)) 
    } else {
      # alternatively just get the unscored submissions in the Evaluation
      # here we get the ones that the 'validation' step (above) marked as validated
      submissionBundles<-synRestGET(sprintf("/evaluation/%s/submission/bundle/all?limit=%s&offset=%s&status=%s",
          evaluation$id, PAGE_SIZE, offset, "VALIDATED")) 
    }
    total<-submissionBundles$totalNumberOfResults
    offset<-offset+PAGE_SIZE
    page<-submissionBundles$results
    if (length(page)>0) {
      for (i in 1:length(page)) {
        # download the file
        submission<-synGetSubmission(page[[i]]$submission$id)
        filePath<-getFileLocation(submission)
        # challenge-specific scoring of the downloaded file goes here
        subStatus<-page[[i]]$submissionStatus
        subStatus$status<-"SCORED"
        # add the score and any other information as submission annotations:
        subStatus$annotations<-generateAnnotations(offset+i)
        statusesToUpdate[[length(statusesToUpdate)+1]]<-subStatus
      }
    }
  }
  updateSubmissionStatusBatch(evaluation, statusesToUpdate)
  message(sprintf("Retrieved and scored %s submissions.", length(statusesToUpdate)))
}

generateAnnotations<-function(i) {
  list(
    stringAnnos=list(
      list(key="aString", value=sprintf("xyz%s",i), isPrivate=FALSE)
    ),
    doubleAnnos=list(
      list(key="correlation", value=runif(1), isPrivate=FALSE)
    ),
    longAnnos  =list(
      list(key="rank", value=floor(runif(1, max=1000)), isPrivate=FALSE)
    )
  )
}

SAMPLE_LEADERBOARD_1 <- "${supertable?path=%2Fevaluation%2Fsubmission%2Fquery%3Fquery%3Dselect%2B%2A%2Bfrom%2Bevaluation%5F"
SAMPLE_LEADERBOARD_2 <- "&paging=true&queryTableResults=true&showIfLoggedInOnly=false&pageSize=25&showRowNumber=false&jsonResultsKeyName=rows&columnConfig0=none%2CSubmission ID%2CobjectId%3B%2CNONE&columnConfig1=none%2CaString%2CaString%3B%2CNONE&columnConfig2=none%2Crank%2Crank%3B%2CNONE&columnConfig3=none%2Ccorrelation%2Ccorrelation%3B%2CNONE&columnConfig4=none%2Cstatus%2Cstatus%3B%2CNONE}"

query<-function(evaluation) {
  start<-Sys.time()
  annotationsFound=FALSE
  while ((Sys.time()-start<WAIT_FOR_QUERY_ANNOTATIONS_SEC) && !annotationsFound) {
    queryResults<-synRestGET(sprintf("/evaluation/submission/query?query=select+*+from+evaluation_%s", evaluation$id))
    total<-queryResults$totalNumberOfResults
    headers<-queryResults$headers
    rows<-queryResults$rows
    if (total<NUM_OF_SUBMISSIONS_TO_CREATE || !any(headers=="aString")) {
      Sys.sleep(2)
    } else {
      message(sprintf("%s retrieved by querying Submission annotations.", length(rows)))
      message(sprintf("To create a leaderboard, add this widget to a wiki page: %s%s%s",
        SAMPLE_LEADERBOARD_1,
          evaluation$id,
          SAMPLE_LEADERBOARD_2
        ))
      annotationsFound = TRUE
    }
  }
  if (!annotationsFound) message("Error:  Annotations have not appeared in query results.")
}

endToEndDemo<-function() {
  tryCatch(
    {
      # create a Challenge project, evaluation queue, etc.
      createdObjects<-setUp()
      evaluation<-createdObjects$evaluation
      # create a handful of challenge submissions
      submitToChallenge(evaluation, createdObjects$participantFile)
      # validate correctness
      # (this can be done at the same time as scoring, below, but we
      # demonstrate doing the two tasks separately)
      validate(evaluation)
      # score the validated submissions
      score(evaluation)
      # query the results (this is the action used by dynamic leader boards
      # viewable in challenge web pages)
      query(evaluation)
    }, 
    finally=tearDown()
  )
}

endToEndDemo()
