package services

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"log"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/phonginreallife/inres/db"
)

// ReleaseService handles release workflow state management
type ReleaseService struct {
	PG *sql.DB
}

// NewReleaseService creates a new ReleaseService
func NewReleaseService(pg *sql.DB) *ReleaseService {
	return &ReleaseService{PG: pg}
}

// CreateRelease creates a new release and all associated workflow steps
func (s *ReleaseService) CreateRelease(req db.CreateReleaseRequest, userID string) (*db.ReleaseWithSteps, error) {
	tx, err := s.PG.Begin()
	if err != nil {
		return nil, fmt.Errorf("failed to begin transaction: %w", err)
	}
	defer tx.Rollback()

	releaseID := uuid.New().String()
	now := time.Now()

	orgID := strings.TrimSpace(req.OrganizationID)
	if orgID == "" {
		return nil, fmt.Errorf("organization_id is required")
	}
	if _, err := uuid.Parse(orgID); err != nil {
		return nil, fmt.Errorf("invalid organization_id: %w", err)
	}

	uid := strings.TrimSpace(userID)
	if uid == "" {
		return nil, fmt.Errorf("authenticated user id is required")
	}
	if _, err := uuid.Parse(uid); err != nil {
		return nil, fmt.Errorf("invalid user id for created_by: %w", err)
	}

	// Convert empty optional UUID strings to nil for Postgres
	var projectID interface{}
	if pid := strings.TrimSpace(req.ProjectID); pid != "" {
		if _, err := uuid.Parse(pid); err != nil {
			return nil, fmt.Errorf("invalid project_id: %w", err)
		}
		projectID = pid
	}
	var confluenceURL interface{}
	if u := strings.TrimSpace(req.ConfluencePageURL); u != "" {
		confluenceURL = u
	}

	_, err = tx.Exec(`
		INSERT INTO releases (id, jira_ticket_id, version, region, status, confluence_page_url, created_by, organization_id, project_id, created_at, updated_at)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $10)`,
		releaseID, req.JiraTicketID, req.Version, req.Region, db.ReleaseStatusDraft,
		confluenceURL, uid, orgID, projectID, now,
	)
	if err != nil {
		return nil, fmt.Errorf("failed to insert release: %w", err)
	}

	// Create all workflow steps in order
	steps := make([]db.ReleaseStep, 0, len(db.AllStepTypes))
	for _, stepType := range db.AllStepTypes {
		stepID := uuid.New().String()
		_, err = tx.Exec(`
			INSERT INTO release_steps (id, release_id, step_type, status)
			VALUES ($1, $2, $3, $4)`,
			stepID, releaseID, stepType, db.StepStatusPending,
		)
		if err != nil {
			return nil, fmt.Errorf("failed to insert step %s: %w", stepType, err)
		}
		steps = append(steps, db.ReleaseStep{
			ID:        stepID,
			ReleaseID: releaseID,
			StepType:  stepType,
			Status:    db.StepStatusPending,
		})
	}

	if err = tx.Commit(); err != nil {
		return nil, fmt.Errorf("failed to commit transaction: %w", err)
	}

	release := &db.ReleaseWithSteps{
		Release: db.Release{
			ID:                releaseID,
			JiraTicketID:      req.JiraTicketID,
			Version:           req.Version,
			Region:            req.Region,
			Status:            db.ReleaseStatusDraft,
			ConfluencePageURL: strings.TrimSpace(req.ConfluencePageURL),
			CreatedBy:         uid,
			OrganizationID:    orgID,
			ProjectID:         strings.TrimSpace(req.ProjectID),
			CreatedAt:         now,
			UpdatedAt:         now,
		},
		Steps: steps,
	}

	s.queueNotification(releaseID, "release_created", map[string]interface{}{
		"version": req.Version,
		"region":  req.Region,
		"jira":    req.JiraTicketID,
	})

	return release, nil
}

// GetRelease returns a release with all its steps and approvals
func (s *ReleaseService) GetRelease(releaseID string) (*db.ReleaseWithSteps, error) {
	release := &db.ReleaseWithSteps{}

	var releaseNotes, plannedChanges []byte
	var prNumber sql.NullInt32
	var confluenceURL, prURL, createdBy, projectID sql.NullString

	err := s.PG.QueryRow(`
		SELECT id, jira_ticket_id, version, region, status, confluence_page_url,
		       release_notes, planned_changes, pr_url, pr_number,
		       created_by, organization_id, project_id, created_at, updated_at
		FROM releases WHERE id = $1`, releaseID,
	).Scan(
		&release.ID, &release.JiraTicketID, &release.Version, &release.Region, &release.Status,
		&confluenceURL, &releaseNotes, &plannedChanges, &prURL, &prNumber,
		&createdBy, &release.OrganizationID, &projectID,
		&release.CreatedAt, &release.UpdatedAt,
	)
	if err != nil {
		if err == sql.ErrNoRows {
			return nil, fmt.Errorf("release not found")
		}
		return nil, fmt.Errorf("failed to get release: %w", err)
	}

	if confluenceURL.Valid {
		release.ConfluencePageURL = confluenceURL.String
	}
	if prURL.Valid {
		release.PRURL = prURL.String
	}
	if prNumber.Valid {
		n := int(prNumber.Int32)
		release.PRNumber = &n
	}
	if createdBy.Valid {
		release.CreatedBy = createdBy.String
	}
	if projectID.Valid {
		release.ProjectID = projectID.String
	}
	release.ReleaseNotes = releaseNotes
	release.PlannedChanges = plannedChanges

	// Fetch steps
	steps, err := s.GetStepsForRelease(releaseID)
	if err != nil {
		return nil, err
	}
	release.Steps = steps

	// Fetch approvals
	approvals, err := s.GetApprovalsForRelease(releaseID)
	if err != nil {
		return nil, err
	}
	release.Approvals = approvals

	return release, nil
}

// ListReleases returns releases filtered by optional criteria
func (s *ReleaseService) ListReleases(orgID, status, region string, limit, offset int) (*db.ReleaseListResponse, error) {
	query := `SELECT id, jira_ticket_id, version, region, status, confluence_page_url,
	                  pr_url, pr_number, created_by, organization_id, project_id,
	                  created_at, updated_at
	           FROM releases WHERE organization_id = $1`
	countQuery := `SELECT COUNT(*) FROM releases WHERE organization_id = $1`
	args := []interface{}{orgID}
	countArgs := []interface{}{orgID}
	paramIdx := 2

	if status != "" {
		query += fmt.Sprintf(" AND status = $%d", paramIdx)
		countQuery += fmt.Sprintf(" AND status = $%d", paramIdx)
		args = append(args, status)
		countArgs = append(countArgs, status)
		paramIdx++
	}
	if region != "" {
		query += fmt.Sprintf(" AND region = $%d", paramIdx)
		countQuery += fmt.Sprintf(" AND region = $%d", paramIdx)
		args = append(args, region)
		countArgs = append(countArgs, region)
		paramIdx++
	}

	// Count total
	var total int
	err := s.PG.QueryRow(countQuery, countArgs...).Scan(&total)
	if err != nil {
		return nil, fmt.Errorf("failed to count releases: %w", err)
	}

	query += fmt.Sprintf(" ORDER BY created_at DESC LIMIT $%d OFFSET $%d", paramIdx, paramIdx+1)
	args = append(args, limit, offset)

	rows, err := s.PG.Query(query, args...)
	if err != nil {
		return nil, fmt.Errorf("failed to query releases: %w", err)
	}
	defer rows.Close()

	releases := []db.Release{}
	for rows.Next() {
		var r db.Release
		var confluenceURL, prURL, createdBy, projectID sql.NullString
		var prNumber sql.NullInt32

		err := rows.Scan(
			&r.ID, &r.JiraTicketID, &r.Version, &r.Region, &r.Status,
			&confluenceURL, &prURL, &prNumber,
			&createdBy, &r.OrganizationID, &projectID,
			&r.CreatedAt, &r.UpdatedAt,
		)
		if err != nil {
			return nil, fmt.Errorf("failed to scan release: %w", err)
		}
		if confluenceURL.Valid {
			r.ConfluencePageURL = confluenceURL.String
		}
		if prURL.Valid {
			r.PRURL = prURL.String
		}
		if prNumber.Valid {
			n := int(prNumber.Int32)
			r.PRNumber = &n
		}
		if createdBy.Valid {
			r.CreatedBy = createdBy.String
		}
		if projectID.Valid {
			r.ProjectID = projectID.String
		}
		releases = append(releases, r)
	}

	return &db.ReleaseListResponse{
		Releases: releases,
		Total:    total,
	}, nil
}

// UpdateReleaseStatus updates the release status and updated_at timestamp
func (s *ReleaseService) UpdateReleaseStatus(releaseID, status string) error {
	res, err := s.PG.Exec(`UPDATE releases SET status = $1 WHERE id = $2`, status, releaseID)
	if err != nil {
		return fmt.Errorf("failed to update release status: %w", err)
	}
	n, _ := res.RowsAffected()
	if n == 0 {
		return fmt.Errorf("release not found")
	}
	return nil
}

// UpdateRelease updates release fields from an UpdateReleaseRequest
func (s *ReleaseService) UpdateRelease(releaseID string, req db.UpdateReleaseRequest) error {
	setClauses := []string{}
	args := []interface{}{}
	paramIdx := 1

	if req.Status != nil {
		setClauses = append(setClauses, fmt.Sprintf("status = $%d", paramIdx))
		args = append(args, *req.Status)
		paramIdx++
	}
	if req.ConfluencePageURL != nil {
		setClauses = append(setClauses, fmt.Sprintf("confluence_page_url = $%d", paramIdx))
		args = append(args, *req.ConfluencePageURL)
		paramIdx++
	}
	if req.ReleaseNotes != nil {
		setClauses = append(setClauses, fmt.Sprintf("release_notes = $%d", paramIdx))
		args = append(args, []byte(*req.ReleaseNotes))
		paramIdx++
	}
	if req.PlannedChanges != nil {
		setClauses = append(setClauses, fmt.Sprintf("planned_changes = $%d", paramIdx))
		args = append(args, []byte(*req.PlannedChanges))
		paramIdx++
	}
	if req.PRURL != nil {
		setClauses = append(setClauses, fmt.Sprintf("pr_url = $%d", paramIdx))
		args = append(args, *req.PRURL)
		paramIdx++
	}
	if req.PRNumber != nil {
		setClauses = append(setClauses, fmt.Sprintf("pr_number = $%d", paramIdx))
		args = append(args, *req.PRNumber)
		paramIdx++
	}

	if len(setClauses) == 0 {
		return nil
	}

	query := "UPDATE releases SET "
	for i, clause := range setClauses {
		if i > 0 {
			query += ", "
		}
		query += clause
	}
	query += fmt.Sprintf(" WHERE id = $%d", paramIdx)
	args = append(args, releaseID)

	_, err := s.PG.Exec(query, args...)
	return err
}

// GetStepsForRelease returns all steps for a release in order
func (s *ReleaseService) GetStepsForRelease(releaseID string) ([]db.ReleaseStep, error) {
	rows, err := s.PG.Query(`
		SELECT id, release_id, step_type, status, output, error_message, started_at, completed_at
		FROM release_steps WHERE release_id = $1
		ORDER BY
			CASE step_type
				WHEN 'jira_fetch' THEN 1
				WHEN 'confluence_parse' THEN 2
				WHEN 'plan_changes' THEN 3
				WHEN 'approve_plan' THEN 4
				WHEN 'apply_yaml' THEN 5
				WHEN 'sops_commands' THEN 6
				WHEN 'create_pr' THEN 7
				WHEN 'approve_pr' THEN 8
				WHEN 'approve_sync' THEN 9
				WHEN 'argocd_sync' THEN 10
				WHEN 'health_check' THEN 11
				WHEN 'approve_deploy' THEN 12
			END`, releaseID)
	if err != nil {
		return nil, fmt.Errorf("failed to query steps: %w", err)
	}
	defer rows.Close()

	var steps []db.ReleaseStep
	for rows.Next() {
		var step db.ReleaseStep
		var output []byte
		var errorMsg sql.NullString
		var startedAt, completedAt sql.NullTime

		err := rows.Scan(&step.ID, &step.ReleaseID, &step.StepType, &step.Status,
			&output, &errorMsg, &startedAt, &completedAt)
		if err != nil {
			return nil, fmt.Errorf("failed to scan step: %w", err)
		}
		step.Output = output
		if errorMsg.Valid {
			step.ErrorMessage = errorMsg.String
		}
		if startedAt.Valid {
			step.StartedAt = &startedAt.Time
		}
		if completedAt.Valid {
			step.CompletedAt = &completedAt.Time
		}
		steps = append(steps, step)
	}
	return steps, nil
}

// UpdateStepStatus updates a step's status and optionally its output/error
func (s *ReleaseService) UpdateStepStatus(releaseID, stepType, status string, output json.RawMessage, errorMsg string) error {
	now := time.Now()

	setClauses := "status = $1"
	args := []interface{}{status}
	paramIdx := 2

	if status == db.StepStatusInProgress {
		setClauses += fmt.Sprintf(", started_at = $%d", paramIdx)
		args = append(args, now)
		paramIdx++
	}
	if status == db.StepStatusCompleted || status == db.StepStatusFailed || status == db.StepStatusSkipped {
		setClauses += fmt.Sprintf(", completed_at = $%d", paramIdx)
		args = append(args, now)
		paramIdx++
	}
	if output != nil {
		setClauses += fmt.Sprintf(", output = $%d", paramIdx)
		args = append(args, []byte(output))
		paramIdx++
	}
	if errorMsg != "" {
		setClauses += fmt.Sprintf(", error_message = $%d", paramIdx)
		args = append(args, errorMsg)
		paramIdx++
	}

	query := fmt.Sprintf("UPDATE release_steps SET %s WHERE release_id = $%d AND step_type = $%d",
		setClauses, paramIdx, paramIdx+1)
	args = append(args, releaseID, stepType)

	res, err := s.PG.Exec(query, args...)
	if err != nil {
		return fmt.Errorf("failed to update step: %w", err)
	}
	n, _ := res.RowsAffected()
	if n == 0 {
		return fmt.Errorf("step not found")
	}
	return nil
}

// GetApprovalsForRelease returns all approvals for a release
func (s *ReleaseService) GetApprovalsForRelease(releaseID string) ([]db.ReleaseApproval, error) {
	rows, err := s.PG.Query(`
		SELECT id, release_id, step_id, approved_by, decision, comment, created_at
		FROM release_approvals WHERE release_id = $1
		ORDER BY created_at`, releaseID)
	if err != nil {
		return nil, fmt.Errorf("failed to query approvals: %w", err)
	}
	defer rows.Close()

	var approvals []db.ReleaseApproval
	for rows.Next() {
		var a db.ReleaseApproval
		var approvedBy, comment sql.NullString
		err := rows.Scan(&a.ID, &a.ReleaseID, &a.StepID, &approvedBy, &a.Decision, &comment, &a.CreatedAt)
		if err != nil {
			return nil, fmt.Errorf("failed to scan approval: %w", err)
		}
		if approvedBy.Valid {
			a.ApprovedBy = approvedBy.String
		}
		if comment.Valid {
			a.Comment = comment.String
		}
		approvals = append(approvals, a)
	}
	return approvals, nil
}

// ApproveStep records an approval/rejection and advances the workflow
func (s *ReleaseService) ApproveStep(releaseID, stepID, userID string, req db.ApproveStepRequest) (*db.ReleaseApproval, error) {
	tx, err := s.PG.Begin()
	if err != nil {
		return nil, fmt.Errorf("failed to begin transaction: %w", err)
	}
	defer tx.Rollback()

	// Verify the step exists and is awaiting approval
	var stepType, stepStatus string
	err = tx.QueryRow(`SELECT step_type, status FROM release_steps WHERE id = $1 AND release_id = $2`,
		stepID, releaseID).Scan(&stepType, &stepStatus)
	if err != nil {
		if err == sql.ErrNoRows {
			return nil, fmt.Errorf("step not found")
		}
		return nil, fmt.Errorf("failed to get step: %w", err)
	}

	if stepStatus != db.StepStatusAwaitingApproval {
		return nil, fmt.Errorf("step is not awaiting approval (current status: %s)", stepStatus)
	}

	// Record the approval
	approvalID := uuid.New().String()
	_, err = tx.Exec(`
		INSERT INTO release_approvals (id, release_id, step_id, approved_by, decision, comment)
		VALUES ($1, $2, $3, $4, $5, $6)`,
		approvalID, releaseID, stepID, userID, req.Decision, req.Comment,
	)
	if err != nil {
		return nil, fmt.Errorf("failed to insert approval: %w", err)
	}

	now := time.Now()

	if req.Decision == db.ApprovalDecisionApproved {
		// Mark step as completed
		_, err = tx.Exec(`UPDATE release_steps SET status = $1, completed_at = $2 WHERE id = $3`,
			db.StepStatusCompleted, now, stepID)
	} else {
		// Mark step as failed on rejection
		_, err = tx.Exec(`UPDATE release_steps SET status = $1, completed_at = $2, error_message = $3 WHERE id = $4`,
			db.StepStatusFailed, now, "Rejected: "+req.Comment, stepID)
		if err == nil {
			// On rejection, mark the release as failed
			_, err = tx.Exec(`UPDATE releases SET status = $1 WHERE id = $2`,
				db.ReleaseStatusFailed, releaseID)
		}
	}
	if err != nil {
		return nil, fmt.Errorf("failed to update step after approval: %w", err)
	}

	if err = tx.Commit(); err != nil {
		return nil, fmt.Errorf("failed to commit: %w", err)
	}

	s.queueNotification(releaseID, "step_"+req.Decision, map[string]interface{}{
		"step_type": stepType,
		"user_id":   userID,
		"comment":   req.Comment,
	})

	return &db.ReleaseApproval{
		ID:         approvalID,
		ReleaseID:  releaseID,
		StepID:     stepID,
		ApprovedBy: userID,
		Decision:   req.Decision,
		Comment:    req.Comment,
		CreatedAt:  now,
	}, nil
}

// CancelRelease cancels an in-progress release
func (s *ReleaseService) CancelRelease(releaseID string) error {
	tx, err := s.PG.Begin()
	if err != nil {
		return fmt.Errorf("failed to begin transaction: %w", err)
	}
	defer tx.Rollback()

	// Only allow cancellation of non-terminal releases
	var currentStatus string
	err = tx.QueryRow(`SELECT status FROM releases WHERE id = $1`, releaseID).Scan(&currentStatus)
	if err != nil {
		if err == sql.ErrNoRows {
			return fmt.Errorf("release not found")
		}
		return err
	}
	if currentStatus == db.ReleaseStatusCompleted || currentStatus == db.ReleaseStatusCancelled {
		return fmt.Errorf("cannot cancel a release in %s status", currentStatus)
	}

	_, err = tx.Exec(`UPDATE releases SET status = $1 WHERE id = $2`, db.ReleaseStatusCancelled, releaseID)
	if err != nil {
		return err
	}

	// Mark all pending/in_progress/awaiting_approval steps as skipped
	_, err = tx.Exec(`
		UPDATE release_steps SET status = $1, completed_at = $2
		WHERE release_id = $3 AND status IN ($4, $5, $6)`,
		db.StepStatusSkipped, time.Now(), releaseID,
		db.StepStatusPending, db.StepStatusInProgress, db.StepStatusAwaitingApproval,
	)
	if err != nil {
		return err
	}

	if err = tx.Commit(); err != nil {
		return err
	}

	s.queueNotification(releaseID, "release_cancelled", nil)
	return nil
}

// queueNotification sends a release notification to the PGMQ queue (best-effort)
func (s *ReleaseService) queueNotification(releaseID, eventType string, data map[string]interface{}) {
	payload := map[string]interface{}{
		"release_id": releaseID,
		"event_type": eventType,
		"data":       data,
		"created_at": time.Now(),
	}
	payloadJSON, err := json.Marshal(payload)
	if err != nil {
		log.Printf("Warning: failed to marshal release notification: %v", err)
		return
	}
	_, err = s.PG.Exec(`SELECT pgmq.send($1, $2)`, "release_notifications", string(payloadJSON))
	if err != nil {
		log.Printf("Warning: failed to queue release notification: %v", err)
	}
}
