package handlers

import (
	"encoding/json"
	"testing"
	"time"
)

func TestProcessPagerDutyWebhook(t *testing.T) {
	handler := &WebhookHandler{}

	tests := []struct {
		name          string
		payload       string
		expectedAlert ProcessedAlert
		checkFields   []string
	}{
		{
			name: "Triggered Incident with P1 priority",
			payload: `{
				"event": {
					"id": "01DCPDF6J30B9XS9D46U7EWRFJ",
					"event_type": "incident.triggered",
					"resource_type": "incident",
					"occurred_at": "2024-01-15T10:30:00Z",
					"agent": {
						"id": "PUSER123",
						"type": "user_reference",
						"name": "John Doe",
						"email": "john@example.com"
					},
					"client": {
						"name": "Monitoring Service"
					},
					"data": {
						"id": "PINC123",
						"type": "incident",
						"html_url": "https://example.pagerduty.com/incidents/PINC123",
						"number": 1234,
						"status": "triggered",
						"incident_key": "srv01/high_cpu",
						"created_at": "2024-01-15T10:30:00Z",
						"title": "High CPU Usage on srv01",
						"urgency": "high",
						"priority": {
							"id": "P1ABC",
							"name": "P1",
							"color": "red"
						},
						"service": {
							"id": "PSVC123",
							"name": "Production API",
							"html_url": "https://example.pagerduty.com/services/PSVC123"
						},
						"assignees": [
							{
								"id": "PUSER456",
								"name": "Jane Smith",
								"email": "jane@example.com"
							}
						],
						"escalation_policy": {
							"id": "PEP123",
							"name": "Production Escalation"
						},
						"description": "CPU usage is above 90% for more than 5 minutes"
					}
				}
			}`,
			expectedAlert: ProcessedAlert{
				AlertName:   "High CPU Usage on srv01",
				Severity:    "critical",
				Status:      "firing",
				Summary:     "High CPU Usage on srv01",
				Description: "CPU usage is above 90% for more than 5 minutes",
				Fingerprint: "srv01/high_cpu",
				Priority:    "P1",
			},
			checkFields: []string{"AlertName", "Severity", "Status", "Summary", "Description", "Fingerprint", "Priority"},
		},
		{
			name: "Acknowledged Incident",
			payload: `{
				"event": {
					"id": "01DCPDF6J30B9XS9D46U7EWRFK",
					"event_type": "incident.acknowledged",
					"resource_type": "incident",
					"occurred_at": "2024-01-15T10:35:00Z",
					"agent": {
						"id": "PUSER456",
						"type": "user_reference",
						"name": "Jane Smith",
						"email": "jane@example.com"
					},
					"data": {
						"id": "PINC123",
						"type": "incident",
						"html_url": "https://example.pagerduty.com/incidents/PINC123",
						"number": 1234,
						"status": "acknowledged",
						"incident_key": "srv01/high_cpu",
						"created_at": "2024-01-15T10:30:00Z",
						"title": "High CPU Usage on srv01",
						"urgency": "high",
						"service": {
							"id": "PSVC123",
							"name": "Production API"
						},
						"assignees": [],
						"escalation_policy": {
							"id": "PEP123",
							"name": "Production Escalation"
						}
					}
				}
			}`,
			expectedAlert: ProcessedAlert{
				AlertName:   "High CPU Usage on srv01",
				Severity:    "high",
				Status:      "firing", // Acknowledged is still active
				Summary:     "High CPU Usage on srv01",
				Fingerprint: "srv01/high_cpu",
			},
			checkFields: []string{"AlertName", "Severity", "Status", "Fingerprint"},
		},
		{
			name: "Resolved Incident",
			payload: `{
				"event": {
					"id": "01DCPDF6J30B9XS9D46U7EWRFL",
					"event_type": "incident.resolved",
					"resource_type": "incident",
					"occurred_at": "2024-01-15T11:00:00Z",
					"agent": {
						"id": "PUSER456",
						"type": "user_reference",
						"name": "Jane Smith",
						"email": "jane@example.com"
					},
					"data": {
						"id": "PINC123",
						"type": "incident",
						"html_url": "https://example.pagerduty.com/incidents/PINC123",
						"number": 1234,
						"status": "resolved",
						"incident_key": "srv01/high_cpu",
						"created_at": "2024-01-15T10:30:00Z",
						"title": "High CPU Usage on srv01",
						"urgency": "high",
						"priority": {
							"id": "P1ABC",
							"name": "P1"
						},
						"service": {
							"id": "PSVC123",
							"name": "Production API"
						},
						"assignees": [],
						"escalation_policy": {
							"id": "PEP123",
							"name": "Production Escalation"
						},
						"resolve_reason": "Issue resolved after scaling up"
					}
				}
			}`,
			expectedAlert: ProcessedAlert{
				AlertName:   "High CPU Usage on srv01",
				Severity:    "critical",
				Status:      "resolved",
				Summary:     "High CPU Usage on srv01",
				Fingerprint: "srv01/high_cpu",
			},
			checkFields: []string{"AlertName", "Status", "Fingerprint"},
		},
		{
			name: "Low Urgency Incident without Priority",
			payload: `{
				"event": {
					"id": "01DCPDF6J30B9XS9D46U7EWRFM",
					"event_type": "incident.triggered",
					"resource_type": "incident",
					"occurred_at": "2024-01-15T12:00:00Z",
					"data": {
						"id": "PINC456",
						"type": "incident",
						"html_url": "https://example.pagerduty.com/incidents/PINC456",
						"number": 1235,
						"status": "triggered",
						"incident_key": "srv02/disk_warning",
						"created_at": "2024-01-15T12:00:00Z",
						"title": "Disk Space Warning on srv02",
						"urgency": "low",
						"service": {
							"id": "PSVC456",
							"name": "Staging Server"
						},
						"assignees": [],
						"escalation_policy": {
							"id": "PEP456",
							"name": "Staging Escalation"
						},
						"description": "Disk usage at 80%"
					}
				}
			}`,
			expectedAlert: ProcessedAlert{
				AlertName:   "Disk Space Warning on srv02",
				Severity:    "low",
				Status:      "firing",
				Summary:     "Disk Space Warning on srv02",
				Description: "Disk usage at 80%",
				Fingerprint: "srv02/disk_warning",
				Priority:    "P3",
			},
			checkFields: []string{"AlertName", "Severity", "Status", "Description", "Fingerprint"},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			var payload map[string]interface{}
			if err := json.Unmarshal([]byte(tt.payload), &payload); err != nil {
				t.Fatalf("Failed to unmarshal payload: %v", err)
			}

			alerts := handler.processPagerDutyWebhook(payload)

			if len(alerts) != 1 {
				t.Fatalf("Expected 1 alert, got %d", len(alerts))
			}

			alert := alerts[0]

			// Check specified fields
			for _, field := range tt.checkFields {
				switch field {
				case "AlertName":
					if alert.AlertName != tt.expectedAlert.AlertName {
						t.Errorf("AlertName = %v, want %v", alert.AlertName, tt.expectedAlert.AlertName)
					}
				case "Severity":
					if alert.Severity != tt.expectedAlert.Severity {
						t.Errorf("Severity = %v, want %v", alert.Severity, tt.expectedAlert.Severity)
					}
				case "Status":
					if alert.Status != tt.expectedAlert.Status {
						t.Errorf("Status = %v, want %v", alert.Status, tt.expectedAlert.Status)
					}
				case "Summary":
					if alert.Summary != tt.expectedAlert.Summary {
						t.Errorf("Summary = %v, want %v", alert.Summary, tt.expectedAlert.Summary)
					}
				case "Description":
					if alert.Description != tt.expectedAlert.Description {
						t.Errorf("Description = %v, want %v", alert.Description, tt.expectedAlert.Description)
					}
				case "Fingerprint":
					if alert.Fingerprint != tt.expectedAlert.Fingerprint {
						t.Errorf("Fingerprint = %v, want %v", alert.Fingerprint, tt.expectedAlert.Fingerprint)
					}
				case "Priority":
					if alert.Priority != tt.expectedAlert.Priority {
						t.Errorf("Priority = %v, want %v", alert.Priority, tt.expectedAlert.Priority)
					}
				}
			}

			// Check Labels
			if alert.Labels["source"] != "pagerduty" {
				t.Errorf("Labels[source] = %v, want pagerduty", alert.Labels["source"])
			}

			// Check timestamp
			if alert.StartsAt.IsZero() {
				t.Error("StartsAt should not be zero")
			}
		})
	}
}

func TestMapPagerDutyPriority(t *testing.T) {
	tests := []struct {
		name     string
		priority string
		expected string
	}{
		{
			name:     "P1 maps to critical",
			priority: "P1",
			expected: "critical",
		},
		{
			name:     "P2 maps to high",
			priority: "P2",
			expected: "high",
		},
		{
			name:     "P3 maps to warning",
			priority: "P3",
			expected: "warning",
		},
		{
			name:     "P4 maps to low",
			priority: "P4",
			expected: "low",
		},
		{
			name:     "P5 maps to info",
			priority: "P5",
			expected: "info",
		},
		{
			name:     "Empty priority defaults to warning",
			priority: "",
			expected: "warning",
		},
		{
			name:     "Lowercase p1 maps to critical",
			priority: "p1",
			expected: "critical",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := mapPagerDutyPriority(tt.priority)
			if result != tt.expected {
				t.Errorf("mapPagerDutyPriority(%s) = %v, want %v", tt.priority, result, tt.expected)
			}
		})
	}
}

func TestMapPagerDutyUrgency(t *testing.T) {
	tests := []struct {
		name     string
		urgency  string
		expected string
	}{
		{
			name:     "High urgency maps to high",
			urgency:  "high",
			expected: "high",
		},
		{
			name:     "Low urgency maps to low",
			urgency:  "low",
			expected: "low",
		},
		{
			name:     "Empty urgency defaults to warning",
			urgency:  "",
			expected: "warning",
		},
		{
			name:     "Unknown urgency defaults to warning",
			urgency:  "medium",
			expected: "warning",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := mapPagerDutyUrgency(tt.urgency)
			if result != tt.expected {
				t.Errorf("mapPagerDutyUrgency(%s) = %v, want %v", tt.urgency, result, tt.expected)
			}
		})
	}
}

func TestPagerDutyWebhookToProcessedAlert(t *testing.T) {
	createdAt, _ := time.Parse(time.RFC3339, "2024-01-15T10:30:00Z")

	webhook := PagerDutyWebhook{
		Event: PagerDutyEvent{
			ID:           "01DCPDF6J30B9XS9D46U7EWRFJ",
			EventType:    "incident.triggered",
			ResourceType: "incident",
			Agent: PagerDutyAgent{
				ID:    "PUSER123",
				Name:  "John Doe",
				Email: "john@example.com",
			},
			Data: PagerDutyIncidentData{
				ID:          "PINC123",
				Number:      1234,
				Status:      "triggered",
				IncidentKey: "srv01/high_cpu",
				CreatedAt:   createdAt,
				Title:       "High CPU Usage",
				Urgency:     "high",
				Priority: &PagerDutyPriority{
					ID:   "P1ABC",
					Name: "P1",
				},
				Service: PagerDutyService{
					ID:   "PSVC123",
					Name: "Production API",
				},
				Assignees: []PagerDutyAssignee{
					{
						ID:    "PUSER456",
						Name:  "Jane Smith",
						Email: "jane@example.com",
					},
				},
				EscalationPolicy: PagerDutyEscalationPolicy{
					ID:   "PEP123",
					Name: "Production Escalation",
				},
				Description: "CPU usage is above 90%",
			},
		},
	}

	alert := webhook.ToProcessedAlert()

	// Check basic fields
	if alert.AlertName != "High CPU Usage" {
		t.Errorf("AlertName = %v, want High CPU Usage", alert.AlertName)
	}
	if alert.Severity != "critical" {
		t.Errorf("Severity = %v, want critical", alert.Severity)
	}
	if alert.Status != "firing" {
		t.Errorf("Status = %v, want firing", alert.Status)
	}
	if alert.Fingerprint != "srv01/high_cpu" {
		t.Errorf("Fingerprint = %v, want srv01/high_cpu", alert.Fingerprint)
	}
	if alert.Priority != "P1" {
		t.Errorf("Priority = %v, want P1", alert.Priority)
	}

	// Check labels
	if alert.Labels["source"] != "pagerduty" {
		t.Errorf("Labels[source] = %v, want pagerduty", alert.Labels["source"])
	}
	if alert.Labels["service_name"] != "Production API" {
		t.Errorf("Labels[service_name] = %v, want Production API", alert.Labels["service_name"])
	}
	if alert.Labels["urgency"] != "high" {
		t.Errorf("Labels[urgency] = %v, want high", alert.Labels["urgency"])
	}

	// Check assignees
	if alert.Labels["assignees"] != "Jane Smith" {
		t.Errorf("Labels[assignees] = %v, want Jane Smith", alert.Labels["assignees"])
	}

	// Check annotations
	if alert.Annotations["escalation_policy"] != "Production Escalation" {
		t.Errorf("Annotations[escalation_policy] = %v, want Production Escalation", alert.Annotations["escalation_policy"])
	}

	// Check timestamp
	if !alert.StartsAt.Equal(createdAt) {
		t.Errorf("StartsAt = %v, want %v", alert.StartsAt, createdAt)
	}
}

func TestPagerDutyWebhookLegacyProcessing(t *testing.T) {
	handler := &WebhookHandler{}

	// Test legacy format (flat structure)
	payload := map[string]interface{}{
		"event": map[string]interface{}{
			"data": map[string]interface{}{
				"title":        "Legacy Alert",
				"status":       "triggered",
				"urgency":      "high",
				"incident_key": "legacy/test",
				"description":  "Legacy format test",
				"html_url":     "https://example.pagerduty.com/incidents/123",
			},
		},
	}

	alerts := handler.processPagerDutyWebhook(payload)

	if len(alerts) != 1 {
		t.Fatalf("Expected 1 alert, got %d", len(alerts))
	}

	alert := alerts[0]
	if alert.AlertName != "Legacy Alert" {
		t.Errorf("AlertName = %v, want Legacy Alert", alert.AlertName)
	}
	if alert.Status != "firing" {
		t.Errorf("Status = %v, want firing", alert.Status)
	}
}

// PagerDuty's "Send Test Event" button emits pagey.ping, and services emit
// service.* events. None of them carry incident data, so before this guard each
// one became an incident with an empty title and no fingerprint to dedupe on.
func TestProcessPagerDutyWebhookIgnoresNonIncidentEvents(t *testing.T) {
	handler := &WebhookHandler{}

	nonIncident := []struct {
		name    string
		payload string
	}{
		{
			name: "pagey.ping from the Send Test Event button",
			payload: `{
				"event": {
					"id": "01TESTPING",
					"event_type": "pagey.ping",
					"resource_type": "pagey",
					"occurred_at": "2024-01-15T10:30:00Z",
					"agent": null,
					"client": null,
					"data": {"message": "Hello from your friend Pagey!", "type": "ping"}
				}
			}`,
		},
		{
			name: "service.updated",
			payload: `{
				"event": {
					"id": "01SVCUPD",
					"event_type": "service.updated",
					"resource_type": "service",
					"occurred_at": "2024-01-15T10:30:00Z",
					"data": {"id": "PSVC123", "type": "service", "name": "Production API"}
				}
			}`,
		},
	}

	for _, tt := range nonIncident {
		t.Run(tt.name, func(t *testing.T) {
			var payload map[string]interface{}
			if err := json.Unmarshal([]byte(tt.payload), &payload); err != nil {
				t.Fatalf("Failed to parse test payload: %v", err)
			}
			alerts := handler.processPagerDutyWebhook(payload)
			if len(alerts) != 0 {
				t.Fatalf("expected no alerts for %s, got %d: %+v", tt.name, len(alerts), alerts)
			}
		})
	}

	// The legacy path reads event_type from the raw map; it must apply the
	// same rule, but keep accepting payloads that have no event_type at all.
	t.Run("legacy path ignores pagey.ping", func(t *testing.T) {
		payload := map[string]interface{}{
			"event": map[string]interface{}{"event_type": "pagey.ping", "data": map[string]interface{}{}},
		}
		if got := handler.processPagerDutyWebhookLegacy(payload); len(got) != 0 {
			t.Fatalf("expected no alerts, got %d", len(got))
		}
	})
	t.Run("legacy path still accepts a payload with no event_type", func(t *testing.T) {
		payload := map[string]interface{}{"title": "Disk full", "status": "triggered"}
		if got := handler.processPagerDutyWebhookLegacy(payload); len(got) != 1 {
			t.Fatalf("expected one alert, got %d", len(got))
		}
	})
}

func TestIsPagerDutyIncidentEvent(t *testing.T) {
	cases := map[string]bool{
		"incident.triggered":          true,
		"incident.resolved":           true,
		"incident.acknowledged":       true,
		"INCIDENT.TRIGGERED":          true,
		"":                            true, // no event_type: let the legacy path decide
		"pagey.ping":                  false,
		"service.updated":             false,
		"incident_workflow.completed": false,
	}
	for eventType, want := range cases {
		if got := isPagerDutyIncidentEvent(eventType); got != want {
			t.Errorf("isPagerDutyIncidentEvent(%q) = %v, want %v", eventType, got, want)
		}
	}
}
