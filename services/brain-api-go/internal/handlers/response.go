package handlers

import (
	"encoding/json"
	"net/http"
)

// jsonResponse writes a JSON response with the given status code.
func jsonResponse(w http.ResponseWriter, data any, statusCode int) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(statusCode)
	json.NewEncoder(w).Encode(data)
}

// jsonError writes a JSON error response.
func jsonError(w http.ResponseWriter, message string, statusCode int) {
	jsonResponse(w, map[string]string{"error": message}, statusCode)
}
