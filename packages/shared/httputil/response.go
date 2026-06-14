// Package httputil provides common HTTP utilities.
package httputil

import (
	"encoding/json"
	"net/http"
)

// Response represents a standard API response envelope.
type Response struct {
	Success bool   `json:"success"`
	Data    any    `json:"data,omitempty"`
	Error   string `json:"error,omitempty"`
}

// ErrorResponse represents an error response.
type ErrorResponse struct {
	Success bool   `json:"success"`
	Error   string `json:"error"`
}

// JSON writes a JSON response with the given status code.
func JSON(w http.ResponseWriter, status int, data any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(data)
}

// Success writes a successful JSON response.
func Success(w http.ResponseWriter, data any) {
	JSON(w, http.StatusOK, Response{
		Success: true,
		Data:    data,
	})
}

// Created writes a 201 Created response.
func Created(w http.ResponseWriter, data any) {
	JSON(w, http.StatusCreated, Response{
		Success: true,
		Data:    data,
	})
}

// NoContent writes a 204 No Content response.
func NoContent(w http.ResponseWriter) {
	w.WriteHeader(http.StatusNoContent)
}

// Error writes an error response with the given status code.
func Error(w http.ResponseWriter, status int, message string) {
	JSON(w, status, ErrorResponse{
		Success: false,
		Error:   message,
	})
}

// BadRequest writes a 400 Bad Request error.
func BadRequest(w http.ResponseWriter, message string) {
	Error(w, http.StatusBadRequest, message)
}

// Unauthorized writes a 401 Unauthorized error.
func Unauthorized(w http.ResponseWriter, message string) {
	if message == "" {
		message = "Unauthorized"
	}
	Error(w, http.StatusUnauthorized, message)
}

// Forbidden writes a 403 Forbidden error.
func Forbidden(w http.ResponseWriter, message string) {
	if message == "" {
		message = "Forbidden"
	}
	Error(w, http.StatusForbidden, message)
}

// NotFound writes a 404 Not Found error.
func NotFound(w http.ResponseWriter, message string) {
	if message == "" {
		message = "Not found"
	}
	Error(w, http.StatusNotFound, message)
}

// InternalError writes a 500 Internal Server Error.
func InternalError(w http.ResponseWriter, message string) {
	if message == "" {
		message = "Internal server error"
	}
	Error(w, http.StatusInternalServerError, message)
}

// ServiceUnavailable writes a 503 Service Unavailable error.
func ServiceUnavailable(w http.ResponseWriter, message string) {
	if message == "" {
		message = "Service unavailable"
	}
	Error(w, http.StatusServiceUnavailable, message)
}
