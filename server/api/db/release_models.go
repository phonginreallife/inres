package db

import (
	"encoding/json"
	"time"
)

// Release status constants
const (
	ReleaseStatusDraft          = "draft"
	ReleaseStatusPlanning       = "planning"
	ReleaseStatusExecuting      = "executing"
	ReleaseStatusAwaitingReview = "awaiting_review"
	ReleaseStatusDeploying      = "deploying"
	ReleaseStatusVerifying      = "verifying"
	ReleaseStatusCompleted      = "completed"
	ReleaseStatusFailed         = "failed"
	ReleaseStatusCancelled      = "cancelled"
)

// Release step type constants
const (
	StepTypeJiraFetch       = "jira_fetch"
	StepTypeConfluenceParse = "confluence_parse"
	StepTypePlanChanges     = "plan_changes"
	StepTypeApprovePlan     = "approve_plan"
	StepTypeApplyYAML       = "apply_yaml"
	StepTypeSOPSCommands    = "sops_commands"
	StepTypeCreatePR        = "create_pr"
	StepTypeApprovePR       = "approve_pr"
	StepTypeApproveSync     = "approve_sync"
	StepTypeArgoCDSync      = "argocd_sync"
	StepTypeHealthCheck     = "health_check"
	StepTypeApproveDeploy   = "approve_deploy"
)

// Release step status constants
const (
	StepStatusPending          = "pending"
	StepStatusInProgress       = "in_progress"
	StepStatusCompleted        = "completed"
	StepStatusFailed           = "failed"
	StepStatusSkipped          = "skipped"
	StepStatusAwaitingApproval = "awaiting_approval"
)

// Approval decision constants
const (
	ApprovalDecisionApproved = "approved"
	ApprovalDecisionRejected = "rejected"
)

// AllStepTypes defines the ordered sequence of steps in a release workflow
var AllStepTypes = []string{
	StepTypeJiraFetch,
	StepTypeConfluenceParse,
	StepTypePlanChanges,
	StepTypeApprovePlan,
	StepTypeApplyYAML,
	StepTypeSOPSCommands,
	StepTypeCreatePR,
	StepTypeApprovePR,
	StepTypeApproveSync,
	StepTypeArgoCDSync,
	StepTypeHealthCheck,
	StepTypeApproveDeploy,
}

// ApprovalStepTypes are steps that require human approval before proceeding
var ApprovalStepTypes = map[string]bool{
	StepTypeApprovePlan:   true,
	StepTypeApprovePR:     true,
	StepTypeApproveSync:   true,
	StepTypeApproveDeploy: true,
}

// Release represents a deployment release workflow
type Release struct {
	ID               string          `json:"id"`
	JiraTicketID     string          `json:"jira_ticket_id"`
	Version          string          `json:"version"`
	Region           string          `json:"region"`
	Status           string          `json:"status"`
	ConfluencePageURL string         `json:"confluence_page_url,omitempty"`
	ReleaseNotes     json.RawMessage `json:"release_notes,omitempty"`
	PlannedChanges   json.RawMessage `json:"planned_changes,omitempty"`
	PRURL            string          `json:"pr_url,omitempty"`
	PRNumber         *int            `json:"pr_number,omitempty"`
	CreatedBy        string          `json:"created_by,omitempty"`
	OrganizationID   string          `json:"organization_id"`
	ProjectID        string          `json:"project_id,omitempty"`
	CreatedAt        time.Time       `json:"created_at"`
	UpdatedAt        time.Time       `json:"updated_at"`

	// Populated via joins / additional queries
	Steps []ReleaseStep `json:"steps,omitempty"`
}

// ReleaseStep represents an individual step in the release workflow
type ReleaseStep struct {
	ID           string          `json:"id"`
	ReleaseID    string          `json:"release_id"`
	StepType     string          `json:"step_type"`
	Status       string          `json:"status"`
	Output       json.RawMessage `json:"output,omitempty"`
	ErrorMessage string          `json:"error_message,omitempty"`
	StartedAt    *time.Time      `json:"started_at,omitempty"`
	CompletedAt  *time.Time      `json:"completed_at,omitempty"`
}

// ReleaseApproval represents an approval decision for a gated step
type ReleaseApproval struct {
	ID         string    `json:"id"`
	ReleaseID  string    `json:"release_id"`
	StepID     string    `json:"step_id"`
	ApprovedBy string    `json:"approved_by,omitempty"`
	Decision   string    `json:"decision"`
	Comment    string    `json:"comment,omitempty"`
	CreatedAt  time.Time `json:"created_at"`
}

// ReleaseWithSteps is a convenience type returned by the API
type ReleaseWithSteps struct {
	Release
	Steps     []ReleaseStep     `json:"steps"`
	Approvals []ReleaseApproval `json:"approvals,omitempty"`
}

// Request / Response DTOs

// CreateReleaseRequest for initiating a new release workflow
type CreateReleaseRequest struct {
	JiraTicketID      string `json:"jira_ticket_id" binding:"required"`
	Version           string `json:"version" binding:"required"`
	Region            string `json:"region" binding:"required"`
	ConfluencePageURL string `json:"confluence_page_url,omitempty"`
	OrganizationID    string `json:"organization_id,omitempty"`
	ProjectID         string `json:"project_id,omitempty"`
}

// UpdateReleaseRequest for updating a release record
type UpdateReleaseRequest struct {
	Status            *string          `json:"status,omitempty"`
	ConfluencePageURL *string          `json:"confluence_page_url,omitempty"`
	ReleaseNotes      *json.RawMessage `json:"release_notes,omitempty"`
	PlannedChanges    *json.RawMessage `json:"planned_changes,omitempty"`
	PRURL             *string          `json:"pr_url,omitempty"`
	PRNumber          *int             `json:"pr_number,omitempty"`
}

// UpdateStepRequest for updating a release step
type UpdateStepRequest struct {
	Status       *string          `json:"status,omitempty"`
	Output       *json.RawMessage `json:"output,omitempty"`
	ErrorMessage *string          `json:"error_message,omitempty"`
}

// ApproveStepRequest for submitting an approval decision
type ApproveStepRequest struct {
	Decision string `json:"decision" binding:"required,oneof=approved rejected"`
	Comment  string `json:"comment,omitempty"`
}

// ReleaseListResponse for paginated release listings
type ReleaseListResponse struct {
	Releases []Release `json:"releases"`
	Total    int       `json:"total"`
}
