package org.sagebionetworks;

import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.io.UnsupportedEncodingException;
import java.nio.charset.Charset;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.Properties;
import java.util.Random;
import java.util.Set;
import java.util.concurrent.TimeUnit;

import org.apache.http.entity.ContentType;
import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;
import org.sagebionetworks.client.SynapseClient;
import org.sagebionetworks.client.SynapseClientImpl;
import org.sagebionetworks.client.SynapseProfileProxy;
import org.sagebionetworks.client.exceptions.SynapseConflictingUpdateException;
import org.sagebionetworks.client.exceptions.SynapseException;
import org.sagebionetworks.client.exceptions.SynapseNotFoundException;
import org.sagebionetworks.evaluation.model.BatchUploadResponse;
import org.sagebionetworks.evaluation.model.Evaluation;
import org.sagebionetworks.evaluation.model.EvaluationStatus;
import org.sagebionetworks.evaluation.model.Submission;
import org.sagebionetworks.evaluation.model.SubmissionBundle;
import org.sagebionetworks.evaluation.model.SubmissionStatus;
import org.sagebionetworks.evaluation.model.SubmissionStatusBatch;
import org.sagebionetworks.evaluation.model.SubmissionStatusEnum;
import org.sagebionetworks.repo.model.ACCESS_TYPE;
import org.sagebionetworks.repo.model.AccessControlList;
import org.sagebionetworks.repo.model.FileEntity;
import org.sagebionetworks.repo.model.PaginatedResults;
import org.sagebionetworks.repo.model.Project;
import org.sagebionetworks.repo.model.ResourceAccess;
import org.sagebionetworks.repo.model.annotation.Annotations;
import org.sagebionetworks.repo.model.annotation.DoubleAnnotation;
import org.sagebionetworks.repo.model.annotation.LongAnnotation;
import org.sagebionetworks.repo.model.annotation.StringAnnotation;
import org.sagebionetworks.repo.model.message.MessageToUser;
import org.sagebionetworks.repo.model.query.QueryTableResults;
import org.sagebionetworks.repo.model.query.Row;
import org.sagebionetworks.utils.MD5ChecksumHelper;


/**
 * Executable template for Challenge scoring application
 * 
 * To use this class, add a properties file 'global.properties' on your class path with the following properties:
 * 
 * The user name and API key for the challenge administrator:
 * ADMIN_USERNAME
 * ADMIN_PASSWORD
 * 
 * The user name and API key for the challenge participant
 * PARTICIPANT_USERNAME
 * PARTICIPANT_PASSWORD
 * 
 */
public class SynapseChallengeTemplate {
	// if true, then tear down at the end, leaving the system in its initial state
	// if false, leave the created objects in place for subsequent use
	private static final boolean TEAR_DOWN_AFTER = true;
	
	// if 'TEAR_DOWN_AFTER' is set to false, then use unique names for projects and the evaluation:
    private static final String CHALLENGE_PROJECT_NAME = "SynapseChallengeTemplate PLEASE CHANGE THIS NAME";
    private static final String CHALLENGE_EVALUATION_NAME = "SynapseChallengeTemplate PLEASE CHANGE THIS NAME";
    private static final String PARTICIPANT_PROJECT_NAME = "SynapseChallengeTemplate Participant PLEASE CHANGE THIS NAME";
    
    // the page size can be bigger, we do this just to demonstrate pagination
    private static int PAGE_SIZE = 20;
    
    // the batch size can be bigger, we do this just to demonstrate batching
    private static int BATCH_SIZE = 20;
    
    // make sure there are multiple batches to handle
    private static int NUM_OF_SUBMISSIONS_TO_CREATE = 2*PAGE_SIZE+1; 

    private static final long WAIT_FOR_QUERY_ANNOTATIONS_MILLIS = 60000L; // a minute
    
	private static final Random random = new Random();
	
	private static final String FILE_CONTENT = "some file content";
	private static final ContentType CONTENT_TYPE = ContentType.create("text/plain", Charset.defaultCharset());
    
	private static Properties properties = null;

    private SynapseClient synapseAdmin;
    private SynapseClient synapseParticipant;
    private Project project;
    private Evaluation evaluation;
    private Project participantProject;
    private FileEntity file;
    
    public static void main( String[] args ) throws Exception {
   		SynapseChallengeTemplate sct = new SynapseChallengeTemplate();
   	    try {
    		// Set Up
    		sct.setUp();
    	
    		// Create Submission
    		sct.submit();
    		
    		// you may wish to do a quick validation, informing the user if the submission is invalid
    		// this could be run frequently, say every minute, to give prompt feedback
    		sct.validate();
    	
    		// Scoring application
    		// This is the part of the code that can be used
    		// as a template for actual scoring applications
    		//
    		// it may be run less frequently than the 'validation' step, e.g. hourly, daily, or perhaps
    		// at the end of a multi-day 'scoring round'
    		sct.score();
    	
    		// Query for leader board
    		sct.query();
    	} finally {
    		// tear down
    		if (TEAR_DOWN_AFTER) sct.tearDown();
    	}
    }
    
    public SynapseChallengeTemplate() throws SynapseException {
    	synapseAdmin = createSynapseClient();
    	String adminUserName = getProperty("ADMIN_USERNAME");
    	String adminPassword = getProperty("ADMIN_PASSWORD");
    	synapseAdmin.login(adminUserName, adminPassword);
    	
    	synapseParticipant = createSynapseClient();
    	String participantUserName = getProperty("PARTICIPANT_USERNAME");
    	String participantPassword = getProperty("PARTICIPANT_PASSWORD");
    	synapseParticipant.login(participantUserName, participantPassword);
    }
    
    /**
     * Create a project for the Challenge.
     * Create the Evaluation queue.
     * Provide access to the participant.
     * @throws UnsupportedEncodingException  
     */
    public void setUp() throws SynapseException, UnsupportedEncodingException {
    	// first make sure the objects to be created don't already exist
    	tearDown();
    	
    	project = new Project();
    	project.setName(CHALLENGE_PROJECT_NAME);
    	project = synapseAdmin.createEntity(project);
    	System.out.println("Created "+project.getId()+" "+project.getName());
    	evaluation = new Evaluation();
    	evaluation.setContentSource(project.getId());
    	evaluation.setName(CHALLENGE_EVALUATION_NAME);
    	evaluation.setStatus(EvaluationStatus.OPEN);
    	evaluation.setSubmissionInstructionsMessage("To submit to the XYZ Challenge, send a tab-delimited file as described here: https://...");
    	evaluation.setSubmissionReceiptMessage("Your submission has been received.   For further information, consult the leaderborad at https://...");
    	evaluation = synapseAdmin.createEvaluation(evaluation);
    	AccessControlList acl = synapseAdmin.getEvaluationAcl(evaluation.getId());
    	Set<ResourceAccess> ras = acl.getResourceAccess();
    	ResourceAccess ra = new ResourceAccess();
    	String participantId = synapseParticipant.getMyProfile().getOwnerId();
    	// Note:  Rather than adding a participant directly to the Evaluation's ACL,
    	// We can add a Team (created for the challenge) to the ACL and then add
    	// the participant to that Team
    	ra.setPrincipalId(Long.parseLong(participantId));
    	Set<ACCESS_TYPE> accessTypes = new HashSet<ACCESS_TYPE>();
    	accessTypes.add(ACCESS_TYPE.SUBMIT);
    	accessTypes.add(ACCESS_TYPE.READ);
    	ra.setAccessType(accessTypes);
    	ras.add(ra);
    	synapseAdmin.updateEvaluationAcl(acl);
    	
    	// participant creates their own project
    	participantProject = new Project();
    	participantProject.setName(PARTICIPANT_PROJECT_NAME);
    	participantProject = synapseParticipant.createEntity(participantProject);
    	// participant creates a file which will be their submission
    	file = new FileEntity();
    	String fileHandleId = synapseParticipant.uploadToFileHandle(
    			FILE_CONTENT.getBytes(Charset.defaultCharset()), CONTENT_TYPE);
    	file.setDataFileHandleId(fileHandleId);
    	file.setParentId(participantProject.getId());
    	file = synapseParticipant.createEntity(file);
    	System.out.println("Created "+participantProject.getId()+" "+participantProject.getName());
   }
    
    private static String findProjectId(SynapseClient synapseClient, String name) throws SynapseException {
		try {
        	JSONObject o = synapseClient.query("select id from entity where \"name\"==\""+name+"\"");
    		long totalNumberOfResults = o.getLong("totalNumberOfResults");
    		if (totalNumberOfResults>1) {
    			System.out.println("Unexpected: "+totalNumberOfResults+" projects are named "+name);
        	} else if (totalNumberOfResults==1L) {
           		JSONObject project = o.getJSONArray("results").getJSONObject(0);
           		String id = project.getString("entity.id");
        		return id;
        	}
        	// otherwise totalNumberOfResults==0
		} catch (JSONException e) {
			throw new RuntimeException(e);
		}
		return null;
    }
    
    public void tearDown() throws SynapseException, UnsupportedEncodingException {
		String projectId = findProjectId(synapseParticipant, PARTICIPANT_PROJECT_NAME);
		if (projectId!=null) {
			synapseParticipant.deleteEntityById(projectId.toString());
		}
		Evaluation evaluation = null;
		try {
			evaluation = synapseAdmin.findEvaluation(CHALLENGE_EVALUATION_NAME);
    	} catch (SynapseNotFoundException e) {
    		// evaluation does not exist
    	}
		if (evaluation!=null) {
			// delete all the Submissions, then the parent Evaluation
	    	long total = Integer.MAX_VALUE;
	    	// keep looping 'til there's nothing left
	       	while (total>0L) {
	       		PaginatedResults<Submission> submissionPGs = 
	       			synapseAdmin.getAllSubmissions(evaluation.getId(), 0L, PAGE_SIZE);
	        	total = (int)submissionPGs.getTotalNumberOfResults();
	        	List<Submission> page = submissionPGs.getResults();
	        	for (int i=0; i<page.size(); i++) {
	        		Submission submission = page.get(i);
	        		String submissionId = submission.getId();
	        		synapseAdmin.deleteSubmission(submissionId);
	        	}
	       	}
	       	synapseAdmin.deleteEvaluation(evaluation.getId());
		}
		projectId = findProjectId(synapseAdmin, CHALLENGE_PROJECT_NAME);
    	if (projectId!=null) {
    		synapseAdmin.deleteEntityById(projectId.toString());
    	}
    }
    
    /**
     * Submit the file to the Evaluation
     * @throws SynapseException
     */
    public void submit() throws SynapseException {
    	for (int i=0; i<NUM_OF_SUBMISSIONS_TO_CREATE; i++) {
	    	Submission submission = new Submission();
	    	submission.setEntityId(file.getId());
	    	submission.setVersionNumber(file.getVersionNumber());
	    	submission.setEvaluationId(evaluation.getId());
	    	submission.setSubmitterAlias("Team Awesome");
	    	synapseParticipant.createIndividualSubmission(submission, file.getEtag());
    	}
    	System.out.println("Submitted "+NUM_OF_SUBMISSIONS_TO_CREATE+" submissions to Evaluation queue: "+evaluation.getId());
    }
    
    /**
     * This demonstrates a lightweight validation step
     * 
     * Beyond simple validation, other criteria could be checked, e.g.
     * whether the user has exceeded a limit on the number of submissions
     * permitted in a given time period
     * 
     * @throws SynapseException
     */
    public void validate() throws SynapseException, IOException {
    	List<SubmissionStatus> statusesToUpdate = new ArrayList<SubmissionStatus>();
    	long total = Integer.MAX_VALUE;
       	for (int offset=0; offset<total; offset+=PAGE_SIZE) {
       			// get the newly RECEIVED Submissions
       		PaginatedResults<SubmissionBundle> submissionPGs = 
       			synapseAdmin.getAllSubmissionBundlesByStatus(evaluation.getId(), SubmissionStatusEnum.RECEIVED, offset, PAGE_SIZE);
        	total = (int)submissionPGs.getTotalNumberOfResults();
        	List<SubmissionBundle> page = submissionPGs.getResults();
        	for (int i=0; i<page.size(); i++) {
        		SubmissionBundle bundle = page.get(i);
        		Submission sub = bundle.getSubmission();
       			File temp = downloadSubmissionFile(sub);
       			// Examine file to decide whether the submission is valid
       			boolean fileIsOK = true;
       			
       			SubmissionStatusEnum newStatus = null;
       			if (fileIsOK) {
       				newStatus = SubmissionStatusEnum.VALIDATED;
       			} else {
       				newStatus = SubmissionStatusEnum.INVALID;
       				// send the user an email message to let them know
       				sendMessage(sub.getUserId(), SUB_ACK_SUBJECT, SUB_ACK_INVALID);
       			}
           		SubmissionStatus status = bundle.getSubmissionStatus();
           		status.setStatus(newStatus);
           	    statusesToUpdate.add(status);
        	}
       	}
       	// we can update all the statuses in a batch
       	updateSubmissionStatusBatch(statusesToUpdate);
    }
    
    private static final String SUB_ACK_SUBJECT = "Submission Acknowledgment";
    private static final String SUB_ACK_INVALID = "Your submission is invalid. Please try again.";
       
    private void sendMessage(String userId, String subject, String body) throws SynapseException {
    	MessageToUser messageMetadata = new MessageToUser();
    	messageMetadata.setRecipients(Collections.singleton(userId));
    	messageMetadata.setSubject(subject);
    	synapseAdmin.sendStringMessage(messageMetadata, body);
    }
    
    /**
     * Note: There are two types of scoring, that in which each submission is scored alone and that
     * in which the entire set of submissions is rescored whenever a new one arrives. 
     * 
     * @throws SynapseException
     */
    public void score() throws SynapseException, IOException {
    	long startTime = System.currentTimeMillis();
    	List<SubmissionStatus> statusesToUpdate = new ArrayList<SubmissionStatus>();
    	long total = Integer.MAX_VALUE;
       	for (int offset=0; offset<total; offset+=PAGE_SIZE) {
       		PaginatedResults<SubmissionBundle> submissionPGs = null;
       		if (true) { 
       			// get ALL the submissions in the Evaluation
       			submissionPGs = synapseAdmin.getAllSubmissionBundles(evaluation.getId(), offset, PAGE_SIZE);
       		} else {
       			// alternatively just get the unscored submissions in the Evaluation
       			// here we get the ones that the 'validation' step (above) marked as validated
       			submissionPGs = synapseAdmin.getAllSubmissionBundlesByStatus(evaluation.getId(), SubmissionStatusEnum.VALIDATED, offset, PAGE_SIZE);
       		}
        	total = (int)submissionPGs.getTotalNumberOfResults();
        	List<SubmissionBundle> page = submissionPGs.getResults();
        	for (int i=0; i<page.size(); i++) {
        		SubmissionBundle bundle = page.get(i);
        		Submission sub = bundle.getSubmission();
        		// at least once, download file and make sure it's correct
        		if (offset==0 && i==0) {
        			File temp = downloadSubmissionFile(sub);
        			String expectedMD5 = MD5ChecksumHelper.getMD5ChecksumForByteArray(FILE_CONTENT.getBytes(Charset.defaultCharset()));
        			String actualMD5 = MD5ChecksumHelper.getMD5Checksum(temp);
        			if (!expectedMD5.equals(actualMD5)) throw new IllegalStateException("Downloaded file does not have expected content.");
        		}
        		SubmissionStatus status = bundle.getSubmissionStatus();
        		SubmissionStatusEnum currentStatus = status.getStatus();
        		if (currentStatus.equals(SubmissionStatusEnum.SCORED)) {
        			// A scorer can filter out submissions which are already scored, are invalid, etc.
        		}
        		Annotations annotations = status.getAnnotations();
        		if (annotations==null) {
        			annotations=new Annotations();
        			status.setAnnotations(annotations);
        		}
    			addAnnotations(annotations, offset+i+1);
    			status.setStatus(SubmissionStatusEnum.SCORED);
    			statusesToUpdate.add(status);
        	}
       	}
       	
       	System.out.println("Retrieved "+total+" submissions for scoring.");
       	
       	updateSubmissionStatusBatch(statusesToUpdate);
       	
       	System.out.println("Scored "+statusesToUpdate.size()+" submissions.");
       	long delta = System.currentTimeMillis() - startTime;
       	System.out.println("Elapsed time for running scoring app: "+formatInterval(delta));
    }
    
    private static final int BATCH_UPLOAD_RETRY_COUNT = 3;
    
    private void updateSubmissionStatusBatch(List<SubmissionStatus> statusesToUpdate) throws SynapseException {
       	// now we have a batch of statuses to update
    	for (int retry=0; retry<BATCH_UPLOAD_RETRY_COUNT; retry++) {
    		try {
		       	String batchToken = null;
		       	for (int offset=0; offset<statusesToUpdate.size(); offset+=BATCH_SIZE) {
		       		SubmissionStatusBatch updateBatch = new SubmissionStatusBatch();
		       		List<SubmissionStatus> batch = new ArrayList<SubmissionStatus>();
		       		for (int i=0; i<BATCH_SIZE && offset+i<statusesToUpdate.size(); i++) {
		       			batch.add(statusesToUpdate.get(offset+i));
		       		}
		       		updateBatch.setStatuses(batch);
		       		boolean isFirstBatch = (offset==0);
		       		updateBatch.setIsFirstBatch(isFirstBatch);
		       		boolean isLastBatch = (offset+BATCH_SIZE)>=statusesToUpdate.size();
		       		updateBatch.setIsLastBatch(isLastBatch);
		       		updateBatch.setBatchToken(batchToken);
		       		BatchUploadResponse response = 
		       				synapseAdmin.updateSubmissionStatusBatch(evaluation.getId(), updateBatch);
		       		batchToken = response.getNextUploadToken();
		       	}
		       	break; // success!
    		} catch (SynapseConflictingUpdateException e) {
    			// we collided with someone else access the Evaluation.  Will retry!
    		}
    	}
    }
    
    private File downloadSubmissionFile(Submission submission) throws SynapseException, IOException {
		String fileHandleId = getFileHandleIdFromEntityBundle(submission.getEntityBundleJSON());
		File temp = File.createTempFile("temp", null);
		synapseAdmin.downloadFromSubmission(submission.getId(), fileHandleId, temp);
		return temp;
    }
    
    private static String getFileHandleIdFromEntityBundle(String s) {
    	try {
	    	JSONObject bundle = new JSONObject(s);
	    	JSONArray fileHandles = (JSONArray)bundle.get("fileHandles");
	    	for (int i=0; i<fileHandles.length(); i++) {
	    		JSONObject fileHandle = fileHandles.getJSONObject(i);
	    		if (!fileHandle.get("concreteType").equals("org.sagebionetworks.repo.model.file.PreviewFileHandle")) {
	    			return (String)fileHandle.get("id");
	    		}
	    	}
	    	throw new IllegalArgumentException("File has no file handle ID");
    	} catch (JSONException e) {
    		throw new RuntimeException(e);
    	}
    }
    
    private static String formatInterval(final long l) {
        final long hr = TimeUnit.MILLISECONDS.toHours(l);
        final long min = TimeUnit.MILLISECONDS.toMinutes(l - TimeUnit.HOURS.toMillis(hr));
        final long sec = TimeUnit.MILLISECONDS.toSeconds(l - TimeUnit.HOURS.toMillis(hr) - TimeUnit.MINUTES.toMillis(min));
        final long ms = TimeUnit.MILLISECONDS.toMillis(l - TimeUnit.HOURS.toMillis(hr) - TimeUnit.MINUTES.toMillis(min) - TimeUnit.SECONDS.toMillis(sec));
        return String.format("%02dh:%02dm:%02d.%03ds", hr, min, sec, ms);
    }
    
    private static final String STRING_ANNOTATION_NAME = "aString";
    
    private static void addAnnotations(Annotations a, int i) {
		StringAnnotation sa = new StringAnnotation();
		// the 'isPrivate' flag should be set to 'true' for information
		// used by the scoring application but not to be revealed to participants
		// to see 'public' annotations requires READ access in the Evaluation's
		// access control list, as the participant has (see setUp(), above). To
		// see 'private' annotatations requires READ_PRIVATE_SUBMISSION access,
		// which the Evaluation admin has by default
		sa.setIsPrivate(false);
		sa.setKey(STRING_ANNOTATION_NAME);
		sa.setValue("xyz"+i);
		List<StringAnnotation> sas = a.getStringAnnos();
		if (sas==null) {
			sas = new ArrayList<StringAnnotation>();
			a.setStringAnnos(sas);
		}
		sas.add(sa);
		DoubleAnnotation da = new DoubleAnnotation();
		da.setIsPrivate(false);
		da.setKey("correlation");
		da.setValue(random.nextDouble());
		List<DoubleAnnotation> das = a.getDoubleAnnos();
		if (das==null) {
			das = new ArrayList<DoubleAnnotation>();
			a.setDoubleAnnos(das);
		}
		das.add(da);
		LongAnnotation la = new LongAnnotation();
		la.setIsPrivate(false);
		la.setKey("rank");
		la.setValue((long)i);
		List<LongAnnotation> las = a.getLongAnnos();
		if (las==null) {
			las = new ArrayList<LongAnnotation>();
			a.setLongAnnos(las);
		}
		las.add(la);   	
    }
    
    /**
     * This demonstrates retrieving submission scoring results using the Evaluation query API.
     * In practice the query would be put in an "API SuperTable" widget in a wiki page in the
     * Synapse Portal.  A sample widget is:
     * ${supertable?path=%2Fevaluation%2Fsubmission%2Fquery%3Fquery%3Dselect%2B%2A%2Bfrom%2Bevaluation%5F2429058&paging=true&queryTableResults=true&showIfLoggedInOnly=false&pageSize=25&showRowNumber=false&jsonResultsKeyName=rows&columnConfig0=none%2CTeam Name%2CsubmitterAlias%3B%2CNONE&columnConfig1=none%2CSubmitter%2CuserId%3B%2CNONE&columnConfig2=none%2CSubmission Name%2Cname%3B%2CNONE&columnConfig3=none%2CSubmission ID%2CobjectId%3B%2CNONE&columnConfig4=epochdate%2CSubmitted On%2CcreatedOn%3B%2CNONE&columnConfig5=none%2CaString%2CaString%3B%2CNONE&columnConfig6=none%2Crank%2Crank%3B%2CNONE&columnConfig7=none%2Ccorrelation%2Ccorrelation%3B%2CNONE}
     * 
     * @throws SynapseException
     */
    public void query() throws SynapseException, InterruptedException {
    	long startTime = System.currentTimeMillis();
    	while (System.currentTimeMillis()<startTime+WAIT_FOR_QUERY_ANNOTATIONS_MILLIS) {
	    	String query = "select * from evaluation_"+evaluation.getId();
	    	QueryTableResults qtr = synapseParticipant.queryEvaluation(query);
	    	long total = qtr.getTotalNumberOfResults();
	    	List<String> headers = qtr.getHeaders();
	    	
	    	if (total<NUM_OF_SUBMISSIONS_TO_CREATE || !headers.contains(STRING_ANNOTATION_NAME)) {
	    		// annotations have not yet been published
	    		Thread.sleep(2000L);
	    		continue;
	    	}
	    	
	    	System.out.println("Columns available for leader board: "+headers);
	    	List<Row> rows = qtr.getRows();
	    	System.out.println(""+rows.size()+" retrieved by querying Submission annotations.");  
	    	System.out.println("To create a leaderboard, add this widget to a wiki page:\n"+
	    			SAMPLE_LEADERBOARD_1+
	    			evaluation.getId()+
	    			SAMPLE_LEADERBOARD_2);
	    	return;
    	}
    	//we reach this line only if we time out
    	System.out.println("Error:  Annotations have not appeared in query results.");
    }
    
    private static final String SAMPLE_LEADERBOARD_1 = "${supertable?path=%2Fevaluation%2Fsubmission%2Fquery%3Fquery%3Dselect%2B%2A%2Bfrom%2Bevaluation%5F";
    private static final String SAMPLE_LEADERBOARD_2 = "&paging=true&queryTableResults=true&showIfLoggedInOnly=false&pageSize=25&showRowNumber=false&jsonResultsKeyName=rows&columnConfig0=none%2CSubmission ID%2CobjectId%3B%2CNONE&columnConfig1=none%2CaString%2CaString%3B%2CNONE&columnConfig2=none%2Crank%2Crank%3B%2CNONE&columnConfig3=none%2Ccorrelation%2Ccorrelation%3B%2CNONE&columnConfig4=none%2Cstatus%2Cstatus%3B%2CNONE}";
    
	public static void initProperties() {
		if (properties!=null) return;
		properties = new Properties();
		InputStream is = null;
    	try {
    		is = SynapseChallengeTemplate.class.getClassLoader().getResourceAsStream("global.properties");
    		properties.load(is);
    	} catch (IOException e) {
    		throw new RuntimeException(e);
    	} finally {
    		if (is!=null) try {
    			is.close();
    		} catch (IOException e) {
    			throw new RuntimeException(e);
    		}
    	}
   }
	
	public static String getProperty(String key) {
		initProperties();
		String commandlineOption = System.getProperty(key);
		if (commandlineOption!=null) return commandlineOption;
		String embeddedProperty = properties.getProperty(key);
		if (embeddedProperty!=null) return embeddedProperty;
		throw new RuntimeException("Cannot find value for "+key);
	}	
	  
	private static SynapseClient createSynapseClient() {
		SynapseClientImpl scIntern = new SynapseClientImpl();
		scIntern.setAuthEndpoint("https://repo-prod.prod.sagebase.org/auth/v1");
		scIntern.setRepositoryEndpoint("https://repo-prod.prod.sagebase.org/repo/v1");
		scIntern.setFileEndpoint("https://repo-prod.prod.sagebase.org/file/v1");
		return SynapseProfileProxy.createProfileProxy(scIntern);
  }

}
