package handlers

import (
	"net/http"
	"strconv"
	"strings"

	"github.com/gin-gonic/gin"
	"github.com/phonginreallife/inres/db"
	"github.com/phonginreallife/inres/services"
)

// ReleaseHandler handles release management REST endpoints
type ReleaseHandler struct {
	releaseService *services.ReleaseService
}

// NewReleaseHandler creates a new ReleaseHandler
func NewReleaseHandler(releaseService *services.ReleaseService) *ReleaseHandler {
	return &ReleaseHandler{releaseService: releaseService}
}

// CreateRelease handles POST /releases
func (h *ReleaseHandler) CreateRelease(c *gin.Context) {
	var req db.CreateReleaseRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid request body", "details": err.Error()})
		return
	}

	// Get user ID from auth context
	userID, exists := c.Get("user_id")
	if !exists {
		c.JSON(http.StatusUnauthorized, gin.H{"error": "User not authenticated"})
		return
	}

	// Org ID from request body, query param, or header (ignore whitespace-only JSON "")
	req.OrganizationID = strings.TrimSpace(req.OrganizationID)
	if req.OrganizationID == "" {
		req.OrganizationID = strings.TrimSpace(c.Query("org_id"))
	}
	if req.OrganizationID == "" {
		req.OrganizationID = strings.TrimSpace(c.GetHeader("X-Org-ID"))
	}
	if req.OrganizationID == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "organization_id is required"})
		return
	}
	req.ProjectID = strings.TrimSpace(req.ProjectID)

	release, err := h.releaseService.CreateRelease(req, userID.(string))
	if err != nil {
		if strings.Contains(err.Error(), "required") ||
			strings.Contains(err.Error(), "invalid ") {
			c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
			return
		}
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to create release", "details": err.Error()})
		return
	}

	c.JSON(http.StatusCreated, release)
}

// GetRelease handles GET /releases/:id
func (h *ReleaseHandler) GetRelease(c *gin.Context) {
	releaseID := c.Param("id")

	release, err := h.releaseService.GetRelease(releaseID)
	if err != nil {
		if err.Error() == "release not found" {
			c.JSON(http.StatusNotFound, gin.H{"error": "Release not found"})
			return
		}
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to get release", "details": err.Error()})
		return
	}

	c.JSON(http.StatusOK, release)
}

// ListReleases handles GET /releases
func (h *ReleaseHandler) ListReleases(c *gin.Context) {
	orgID := c.Query("org_id")
	if orgID == "" {
		orgID = c.GetHeader("X-Org-ID")
	}
	if orgID == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "org_id is required"})
		return
	}

	status := c.Query("status")
	region := c.Query("region")

	limit := 20
	if l := c.Query("limit"); l != "" {
		if parsed, err := strconv.Atoi(l); err == nil && parsed > 0 && parsed <= 100 {
			limit = parsed
		}
	}
	offset := 0
	if o := c.Query("offset"); o != "" {
		if parsed, err := strconv.Atoi(o); err == nil && parsed >= 0 {
			offset = parsed
		}
	}

	result, err := h.releaseService.ListReleases(orgID, status, region, limit, offset)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to list releases", "details": err.Error()})
		return
	}

	c.JSON(http.StatusOK, result)
}

// UpdateRelease handles PATCH /releases/:id
func (h *ReleaseHandler) UpdateRelease(c *gin.Context) {
	releaseID := c.Param("id")

	var req db.UpdateReleaseRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid request body", "details": err.Error()})
		return
	}

	if err := h.releaseService.UpdateRelease(releaseID, req); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to update release", "details": err.Error()})
		return
	}

	c.JSON(http.StatusOK, gin.H{"message": "Release updated"})
}

// UpdateStep handles PATCH /releases/:id/steps/:step_type
func (h *ReleaseHandler) UpdateStep(c *gin.Context) {
	releaseID := c.Param("id")
	stepType := c.Param("step_type")

	var req db.UpdateStepRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid request body", "details": err.Error()})
		return
	}

	status := ""
	if req.Status != nil {
		status = *req.Status
	}
	var output []byte
	if req.Output != nil {
		output = *req.Output
	}
	errorMsg := ""
	if req.ErrorMessage != nil {
		errorMsg = *req.ErrorMessage
	}

	if err := h.releaseService.UpdateStepStatus(releaseID, stepType, status, output, errorMsg); err != nil {
		if err.Error() == "step not found" {
			c.JSON(http.StatusNotFound, gin.H{"error": "Step not found"})
			return
		}
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to update step", "details": err.Error()})
		return
	}

	c.JSON(http.StatusOK, gin.H{"message": "Step updated"})
}

// ApproveStep handles POST /releases/:id/steps/:step_id/approve
func (h *ReleaseHandler) ApproveStep(c *gin.Context) {
	releaseID := c.Param("id")
	stepID := c.Param("step_id")

	var req db.ApproveStepRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid request body", "details": err.Error()})
		return
	}

	userID, exists := c.Get("user_id")
	if !exists {
		c.JSON(http.StatusUnauthorized, gin.H{"error": "User not authenticated"})
		return
	}

	approval, err := h.releaseService.ApproveStep(releaseID, stepID, userID.(string), req)
	if err != nil {
		status := http.StatusInternalServerError
		if err.Error() == "step not found" {
			status = http.StatusNotFound
		} else if err.Error() != "" && len(err.Error()) > 0 {
			// Check for validation errors
			errMsg := err.Error()
			if len(errMsg) > 18 && errMsg[:18] == "step is not awaiti" {
				status = http.StatusConflict
			}
		}
		c.JSON(status, gin.H{"error": err.Error()})
		return
	}

	c.JSON(http.StatusOK, approval)
}

// CancelRelease handles POST /releases/:id/cancel
func (h *ReleaseHandler) CancelRelease(c *gin.Context) {
	releaseID := c.Param("id")

	if err := h.releaseService.CancelRelease(releaseID); err != nil {
		status := http.StatusInternalServerError
		if err.Error() == "release not found" {
			status = http.StatusNotFound
		} else if len(err.Error()) > 10 && err.Error()[:10] == "cannot can" {
			status = http.StatusConflict
		}
		c.JSON(status, gin.H{"error": err.Error()})
		return
	}

	c.JSON(http.StatusOK, gin.H{"message": "Release cancelled"})
}

// GetReleaseStatus handles GET /releases/:id/status (lightweight)
func (h *ReleaseHandler) GetReleaseStatus(c *gin.Context) {
	releaseID := c.Param("id")

	release, err := h.releaseService.GetRelease(releaseID)
	if err != nil {
		if err.Error() == "release not found" {
			c.JSON(http.StatusNotFound, gin.H{"error": "Release not found"})
			return
		}
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}

	// Build a lightweight status response
	stepStatuses := make([]gin.H, 0, len(release.Steps))
	for _, step := range release.Steps {
		stepStatuses = append(stepStatuses, gin.H{
			"step_type": step.StepType,
			"status":    step.Status,
		})
	}

	c.JSON(http.StatusOK, gin.H{
		"id":      release.ID,
		"status":  release.Status,
		"version": release.Version,
		"region":  release.Region,
		"pr_url":  release.PRURL,
		"steps":   stepStatuses,
	})
}
