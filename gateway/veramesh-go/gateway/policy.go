package gateway

type SecurityScheme struct {
	Type   string   `json:"type"`
	Scopes []string `json:"scopes"`
}

type ToolPolicy struct {
	Name         string
	Operation    string
	Scopes       []string
	Capabilities []string
	ReadOnly     bool
	Destructive  bool
	Idempotent   bool
	OpenWorld    bool
	Description  string
}

func (p ToolPolicy) SecuritySchemes() []SecurityScheme {
	return []SecurityScheme{{Type: "oauth2", Scopes: append([]string(nil), p.Scopes...)}}
}

var Scopes = map[string]string{
	"computer.profile": "Read authenticated workstation identity and path health.",
	"computer.read": "Read/search filesystem content inside workstation-allowed roots.",
	"computer.write": "Modify filesystem content inside workstation-allowed roots.",
	"computer.process": "Start, inspect, interact with, and terminate VeraPort-managed processes.",
}

var PublicTools = []ToolPolicy{
	{Name:"computer_info", Operation:"machine.info", Scopes:[]string{"computer.profile"}, Capabilities:[]string{}, ReadOnly:true, Idempotent:true, Description:"Read authenticated workstation identity and live VeraPort path health."},
	{Name:"read_file", Operation:"fs.read_text", Scopes:[]string{"computer.read"}, Capabilities:[]string{"fs.read"}, ReadOnly:true, Idempotent:true, Description:"Read bounded text from an absolute path inside workstation-allowed roots."},
	{Name:"read_bytes", Operation:"fs.read_bytes", Scopes:[]string{"computer.read"}, Capabilities:[]string{"fs.read"}, ReadOnly:true, Idempotent:true, Description:"Read a version-bound byte range from an absolute allowed path."},
	{Name:"stat_path", Operation:"fs.stat", Scopes:[]string{"computer.read"}, Capabilities:[]string{"fs.read"}, ReadOnly:true, Idempotent:true, Description:"Read metadata for an absolute allowed path without following the final symlink."},
	{Name:"list_directory", Operation:"fs.list_dir", Scopes:[]string{"computer.read"}, Capabilities:[]string{"fs.read"}, ReadOnly:true, Idempotent:true, Description:"List one allowed directory with deterministic bounded pagination."},
	{Name:"search_files", Operation:"fs.search", Scopes:[]string{"computer.read"}, Capabilities:[]string{"fs.read"}, ReadOnly:true, Idempotent:true, Description:"Search path names below an allowed root with explicit depth/result/scan bounds."},
	{Name:"search_content", Operation:"fs.search_content", Scopes:[]string{"computer.read"}, Capabilities:[]string{"fs.read"}, ReadOnly:true, Idempotent:true, Description:"Search bounded UTF-8-decoded file content below an allowed root."},
	{Name:"write_file", Operation:"fs.write_text", Scopes:[]string{"computer.write"}, Capabilities:[]string{"fs.write"}, Destructive:true, Idempotent:true, Description:"Atomically write text to an absolute allowed path."},
	{Name:"append_file", Operation:"fs.append_text", Scopes:[]string{"computer.write"}, Capabilities:[]string{"fs.write"}, Description:"Append text to an absolute allowed path."},
	{Name:"make_directory", Operation:"fs.mkdir", Scopes:[]string{"computer.write"}, Capabilities:[]string{"fs.write"}, Idempotent:true, Description:"Create a directory inside workstation-allowed roots."},
	{Name:"move_path", Operation:"fs.move", Scopes:[]string{"computer.write"}, Capabilities:[]string{"fs.write"}, Destructive:true, Description:"Move or rename a path when source and destination are both authorized."},
	{Name:"replace_text", Operation:"fs.replace_text", Scopes:[]string{"computer.read","computer.write"}, Capabilities:[]string{"fs.read","fs.write"}, Destructive:true, Description:"Atomically replace an exact expected number of text matches."},
	{Name:"run_process", Operation:"process.exec", Scopes:[]string{"computer.process"}, Capabilities:[]string{"process.exec"}, Destructive:true, OpenWorld:true, Description:"Run one bounded argv-vector process without an implicit shell."},
	{Name:"start_process", Operation:"process.start", Scopes:[]string{"computer.process"}, Capabilities:[]string{"process.exec","process.inspect","process.interact","process.control"}, Destructive:true, OpenWorld:true, Description:"Start a VeraPort-managed process and return an opaque public handle."},
	{Name:"list_processes", Operation:"process.list", Scopes:[]string{"computer.process"}, Capabilities:[]string{"process.inspect"}, ReadOnly:true, Idempotent:true, Description:"List managed processes owned by the authenticated gateway actor."},
	{Name:"process_status", Operation:"process.status", Scopes:[]string{"computer.process"}, Capabilities:[]string{"process.inspect"}, ReadOnly:true, Idempotent:true, Description:"Read status for one managed public process handle."},
	{Name:"process_output", Operation:"process.output", Scopes:[]string{"computer.process"}, Capabilities:[]string{"process.inspect"}, ReadOnly:true, Idempotent:true, Description:"Read bounded stdout/stderr chunks for one managed process."},
	{Name:"process_input", Operation:"process.input", Scopes:[]string{"computer.process"}, Capabilities:[]string{"process.interact"}, Destructive:true, OpenWorld:true, Description:"Send bounded UTF-8 input to one managed process."},
	{Name:"terminate_process", Operation:"process.terminate", Scopes:[]string{"computer.process"}, Capabilities:[]string{"process.control","process.inspect"}, Destructive:true, Idempotent:true, Description:"Terminate one managed process owned by the authenticated actor."},
	{Name:"release_process", Operation:"gateway.release_process", Scopes:[]string{"computer.process"}, Capabilities:[]string{"process.control"}, Destructive:true, Idempotent:true, Description:"Release a managed process lease; a still-running child is terminated."},
}

var PublicToolByName = func() map[string]ToolPolicy {
	out := make(map[string]ToolPolicy, len(PublicTools))
	for _, p := range PublicTools { out[p.Name] = p }
	return out
}()

func ToolSupported(cfg *Config, p ToolPolicy) bool {
	if p.Name == "computer_info" { return true }
	ops := cfg.OperationSet()
	if _, ok := ops["lane.open"]; !ok { return false }
	if _, ok := ops["lane.close"]; !ok { return false }
	caps := cfg.RequestedSet()
	for _, cap := range p.Capabilities {
		if _, ok := caps[cap]; !ok { return false }
	}
	switch p.Operation {
	case "gateway.release_process":
		_, start := ops["process.start"]
		_, terminate := ops["process.terminate"]
		return start && terminate
	}
	switch p.Name {
	case "list_processes", "process_status", "process_output", "process_input", "terminate_process":
		if _, ok := ops["process.start"]; !ok { return false }
		_, ok := ops[p.Operation]
		return ok
	default:
		_, ok := ops[p.Operation]
		return ok
	}
}
