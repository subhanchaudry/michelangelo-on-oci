// Evaluate the exact admission expressions with allowed and adversarial objects.
package main

import (
	"encoding/json"
	"fmt"
	"github.com/google/cel-go/cel"
	"os"
)

func main() {
	raw, err := os.ReadFile(os.Args[1])
	if err != nil {
		panic(err)
	}
	var cases []struct {
		Name, Expression string
		Object           map[string]any
		Allowed          bool
		Operation        string
	}
	if err = json.Unmarshal(raw, &cases); err != nil {
		panic(err)
	}
	env, err := cel.NewEnv(cel.Variable("object", cel.DynType), cel.Variable("oldObject", cel.DynType), cel.Variable("request", cel.DynType))
	if err != nil {
		panic(err)
	}
	for _, test := range cases {
		ast, issues := env.Compile(test.Expression)
		if issues.Err() != nil {
			panic(fmt.Sprintf("%s: compile: %v", test.Name, issues.Err()))
		}
		program, err := env.Program(ast)
		if err != nil {
			panic(err)
		}
		operation := test.Operation
		if operation == "" {
			operation = "CREATE"
		}
		result, _, err := program.Eval(map[string]any{"object": test.Object, "oldObject": test.Object, "request": map[string]any{"operation": operation}})
		allowed := err == nil && result.Value() == true
		if allowed != test.Allowed {
			panic(fmt.Sprintf("%s: allowed=%v expected=%v error=%v", test.Name, allowed, test.Allowed, err))
		}
	}
	fmt.Printf("%d runtime admission cases passed\n", len(cases))
}
