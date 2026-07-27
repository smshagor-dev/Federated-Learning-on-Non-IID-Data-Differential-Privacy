package security

import (
	"testing"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/auth"
)

func TestAllowsMatrix(t *testing.T) {
	cases := []struct {
		role  auth.Role
		perm  Permission
		allow bool
	}{
		{auth.RoleAdmin, PermCoordinatorKeysRotate, true},
		{auth.RoleAdmin, PermAuditReadDetailed, true},
		{auth.RoleResearcher, PermCoordinatorKeysRotate, false},
		{auth.RoleResearcher, PermCoordinatorKeysRead, true},
		{auth.RoleResearcher, PermAuditReadDetailed, false},
		{auth.RoleViewer, PermWorkersRead, true},
		{auth.RoleViewer, PermWorkersSuspend, false},
		{auth.RoleViewer, PermCoordinatorKeysRead, false},
		{auth.RoleService, PermTransportRead, false},
		{auth.RoleService, PermWorkersSuspend, false},
	}
	for _, testCase := range cases {
		if got := Allows(testCase.role, testCase.perm); got != testCase.allow {
			t.Errorf("Allows(%s, %s) = %v, want %v", testCase.role, testCase.perm, got, testCase.allow)
		}
	}
}

func TestServiceRoleNeverAutomaticallyAdmin(t *testing.T) {
	for perm := range rolePermissions[auth.RoleAdmin] {
		if Allows(auth.RoleService, perm) {
			t.Errorf("SERVICE role must not automatically receive ADMIN permission %s", perm)
		}
	}
}

func TestHasScopeChecksExplicitGrantOnly(t *testing.T) {
	scopes := []string{string(PermCoordinatorKeysRotate)}
	if !HasScope(scopes, PermCoordinatorKeysRotate) {
		t.Fatal("expected explicit scope grant to be honored")
	}
	if HasScope(scopes, PermWorkersRevoke) {
		t.Fatal("expected a permission not in the explicit scope list to be denied")
	}
	if HasScope(nil, PermCoordinatorKeysRotate) {
		t.Fatal("expected a nil scope list to grant nothing")
	}
}
