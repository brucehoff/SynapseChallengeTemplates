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

# !!! TEMPORARY: Run on 'staging' !!!
synSetEndpoints('http://localhost:8080/services-repository-develop-SNAPSHOT/repo/v1', 
  'http://localhost:8080/services-repository-develop-SNAPSHOT/auth/v1', 
  'http://localhost:8080/services-repository-develop-SNAPSHOT/file/v1')
#synapseLogin()
synapseLogin(username="migrationAdmin@sagebase.org", apiKey="chjDV/MUjPjrf0o93wEX9ul2SWWHZHIuwpVp16q3lSsihvYZTzYcQ5sHyhGbVJ4ymWkiIbJq3ueacp233JVizw==", rememberMe=F)
# !!! end TEMPORARY: Run on 'staging' !!!



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

WAIT_FOR_QUERY_ANNOTATIONS_MILLIS <- 60000L # a minute


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
    submit(evaluation=evaluation, entity=participantFile, teamName="Team Awesome", silent=TRUE)
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

score<-function() {
  
}

query<-function() {
  
}

endToEndDemo<-function() {
  tryCatch(
    {
      createdObjects<-setUp()
      evaluation<-createdObjects$evaluation
      submitToChallenge(evaluation, createdObjects$participantFile)
      validate(evaluation)
      score()
      query()
    }, 
    finally=tearDown()
  )
}

endToEndDemo()
