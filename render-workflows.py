# call this script as:
# python docs/render-workflows.py docs/render-workflows.html workflow-templates/*.yaml
# then open the generated docs/render-workflows.html in a browser

import yaml
import sys
import re
from dataclasses import dataclass

SHOW_ARTIFACTS = True
SHOW_ALL_TEMPLATES = False
SHOW_SHARED_TEMPLATES = False


@dataclass
class Workflow:
    id: str
    name: str
    nodes: list["Node"]

@dataclass
class Node:
    id: str
    name: str
    image: str
    script: str
    tasks: list["Task"]
    incoming_count: int
    input_params: dict
    output_params: dict
    input_artifacts: list[str]
    output_artifacts: list[str]
    is_entrypoint: bool = False

@dataclass
class Task:
    id: str
    name: str
    dependencies: [str]
    when: str
    ref_node: str
    input_params: dict
    input_artifacts: list[str]

def main():
    if len(sys.argv) < 3:
        print("Usage: python script.py <html-file.html> <workflow-file.yaml> ...")
        sys.exit(1)

    workflows = {}
    for workflow_file in sys.argv[2:]:
        w_yaml = parse_yaml(workflow_file)
        if not w_yaml:
            print(f"Failed to parse {workflow_file}")
            continue
        w = make_workflow(w_yaml)
        workflows[w.name] = w

    nodes = {}
    for w in workflows.values():
        for n in w.nodes:
            nodes[n.id] = n
    for w in workflows.values():
        for n in w.nodes:
            for t in n.tasks:
                if nodes.get(t.ref_node):
                    nodes[t.ref_node].incoming_count += 1

    generate_mermaid(workflows.values(), nodes)

def make_workflow(w_yaml):
    w_name = w_yaml['metadata']['name']
    w = Workflow(id=w_name, name=w_name, nodes=[])
    for n_yaml in w_yaml['spec']['templates']:
        n_id = w_name + "__" + n_yaml['name']
        image = n_yaml.get('container', {}).get('image', '').split("/")[-1].split(":")[0].replace("ai-workflow-", "")
        script = n_yaml.get('script', {}).get('image', "")

        input_params = {}
        for p_yaml in n_yaml.get('inputs', {}).get('parameters', []):
            if 'value' in p_yaml and "{{" not in p_yaml['value']:
                input_params[p_yaml['name']] = p_yaml['value']

        output_params = {}
        for p_yaml in n_yaml.get('outputs', {}).get('parameters', []):
            if 'value' in p_yaml and "{{" not in p_yaml['value']:
                output_params[p_yaml['name']] = p_yaml['value']

        input_artifacts = []
        for a_yaml in n_yaml.get('inputs', {}).get('artifacts', []):
            input_artifacts.append(a_yaml['name'])

        output_artifacts = []
        for a_yaml in n_yaml.get('outputs', {}).get('artifacts', []):
            output_artifacts.append(a_yaml['name'])

        n = Node(
            id=n_id,
            name=n_yaml['name'],
            image=image,
            script=script,
            tasks=[],
            incoming_count=0,
            input_params=input_params,
            output_params=output_params,
            input_artifacts=input_artifacts,
            output_artifacts=output_artifacts
        )
        tasks_yaml = n_yaml.get('dag', {}).get('tasks', [])
        for t_yaml in tasks_yaml or []:
            t_id = n_id + "__" + t_yaml['name']
            dependencies = [n_id + "__" + d for d in t_yaml.get('dependencies', [])]
            if 'depends' in t_yaml:
                for d in parse_depends(t_yaml['depends']):
                    dependencies.append(n_id + "__" + d)
            dependencies = list(dict.fromkeys(dependencies))

            ref_node = None
            if 'templateRef' in t_yaml:
                ref_node = t_yaml['templateRef']['name'] + "__" + t_yaml['templateRef']['template']
            elif 'template' in t_yaml:
                ref_node = w_name + "__" + t_yaml['template']

            params = {}
            for p_yaml in t_yaml.get('arguments', {}).get('parameters', []):
                if 'value' in p_yaml and "{{" not in p_yaml['value']:
                    params[p_yaml['name']] = p_yaml['value']

            def parse_artifacts(s):
                s = s.strip()
                if s.startswith("{{") and s.endswith("}}"):
                    s = s[2:-2].strip()
                ret = dict()
                matches = re.findall(r'inputs.artifacts.([\w\-_]+)\b', s)
                if matches:
                    ret.update(dict.fromkeys(matches))
                matches = re.findall(r'input.artifacts.([\w\-_]+)', s)
                if matches:
                    ret.update(dict.fromkeys(matches))
                matches = re.findall(r'tasks.([\w\-_]+).outputs.artifacts.([\w\-_]+)', s)
                if matches:
                    ret.update({m[0] + "__" + m[1] :"" for m in matches})
                return list(ret.keys()) if ret else [s]

            input_artifacts = []
            for i_yaml in t_yaml.get('arguments', {}).get('artifacts', []):
                if i_yaml.get('from'):
                    input_artifacts.extend([n.id + "__" + a_id for a_id in parse_artifacts(i_yaml['from'])])
                elif i_yaml.get('fromExpression'):
                    match = re.match(r'(.*)\?(.*):(.*)', i_yaml['fromExpression'])
                    if match:
                        _, a1, a2 = match.groups()
                        input_artifacts.extend([n.id + "__" + a_id for a_id in parse_artifacts(a1)])
                        input_artifacts.extend([n.id + "__" + a_id for a_id in parse_artifacts(a2)])
                    else:
                        raise ValueError("Unknown artifact type: " + str(i_yaml))
                else:
                    raise ValueError("Unknown artifact type: " + str(i_yaml))
            t = Task(
                id=t_id, name=t_yaml['name'],
                dependencies=dependencies,
                when=t_yaml.get('when'),
                ref_node=ref_node,
                input_params=params,
                input_artifacts=input_artifacts
            )
            n.tasks.append(t)
            n.tasks.sort(key=lambda t: t.name)
        if w_yaml.get('spec', {}).get('entrypoint') == n_yaml['name']:
            n.is_entrypoint = True
        n.tasks.sort(key=lambda t: t.name)
        w.nodes.append(n)
    return w

def show_node(n):
    if n.is_entrypoint:
        return True
    if n.tasks:
        return True
    if SHOW_ALL_TEMPLATES:
        return True
    if SHOW_SHARED_TEMPLATES:
        return n.incoming_count > 1
    return False

def generate_mermaid(workflows, nodes):
    bases = []
    # render nodes and tasks
    for w in workflows:
        bases.append(f"subgraph {w.name}")
        bases.append("    direction LR")
        bases.append("    style "+w.name+" fill:#fafaff;")
        for n in w.nodes:
            if show_node(n):
                # render the node itself
                name = f"<span style=\"font-size:20px\">{n.name}</span>"
                if n.image:
                    name += f"\\n<span style=\"color:green\">image: {n.image}</span>"
                if n.script:
                    name += f"\\n<span style=\"color:green\">script: {n.script}</span>"
                if n.input_params:
                    name += f"<pre style=\"color:darkgray;margin-top:8px\">"
                    for p in n.input_params:
                        name += f"{p}={n.input_params[p]}<br>"
                    name += "</pre>"
                bases.append("    "+n.id+"{{"+name+"}}")
                bases.append("    style "+n.id+" fill:lightgray,stroke:#aaa;")
                # render output artifacts
                if SHOW_ARTIFACTS:
                    for a in n.input_artifacts:
                        a_id = n.id + "__" + a
                        name = f"<span style=\"font-size:20px\">{a}</span>"
                        bases.append(f"    {a_id}(<b>{name}</b>)")
                        bases.append("    style "+a_id+" fill:gold,stroke:#222;")
            for t in n.tasks:
                # render the task
                name = f"<span style=\"font-size:20px\">{t.name}</span>"
                if t.when:
                    when = t.when.replace("{{", "").replace("}}", "")
                    name += f"<pre style=\"color:red\">when: {when}</pre>"
                if t.ref_node and nodes.get(t.ref_node) and not show_node(nodes[t.ref_node]):
                    if nodes[t.ref_node].image:
                        name += f"<pre style=\"color:green\">image: {nodes[t.ref_node].image}</pre>"
                    if nodes[t.ref_node].script:
                        name += f"<pre style=\"color:green\">script: {nodes[t.ref_node].script}</pre>"
                if t.input_params:
                    name += f"<pre style=\"color:dimgray;margin-top:8px\">"
                    for p in t.input_params:
                        name += f"{p}={t.input_params[p]}<br>"
                    name += "</pre>"
                bases.append(f"    {t.id}[{name}]")
                bases.append("    style "+t.id+" fill:white;")
                # render artifacts
                if SHOW_ARTIFACTS:
                    for a in t.input_artifacts:
                        name = f"<span style=\"font-size:20px\">{a}</span>"
                        # bases.append(f"    {a_id}(<b>{name}</b>)")
                        # bases.append("    style "+a_id+" fill:gold,stroke:#222;")
                if SHOW_ARTIFACTS and t.ref_node and nodes.get(t.ref_node):
                    for a in nodes[t.ref_node].output_artifacts:
                        a_id = t.id + "__" + a
                        name = f"<span style=\"font-size:20px\">{a}</span>"
                        bases.append(f"    {a_id}(<b>{name}</b>)")
                        bases.append("    style "+a_id+" fill:gold,stroke:#222;")

        bases.append("end")

    # render links
    flow_lines = []
    interpackage_lines = []
    artifact_lines = []
    links = []
    for w in workflows:
        for n in w.nodes:
            if show_node(n) and SHOW_ARTIFACTS:
                for a in n.input_artifacts:
                    # link nodes to their input artifacts
                    a_id = n.id + "__" + a
                    artifact_lines.append(str(len(links)))
                    links.append(f"{a_id} --- {n.id}")
            for t in n.tasks:
                if not t.dependencies:
                    # link nodes to their first (independent) tasks
                    flow_lines.append(str(len(links)))
                    links.append(f"{n.id} --> {t.id}")
            for t in n.tasks:
                for d in t.dependencies:
                    # link tasks to their dependencies
                    flow_lines.append(str(len(links)))
                    links.append(f"{d} --> {t.id}")
                if t.ref_node:
                    # link tasks to their template nodes
                    if nodes.get(t.ref_node) and show_node(nodes[t.ref_node]):
                        interpackage_lines.append(str(len(links)))
                        links.append(f"{t.id} -.-> {t.ref_node}")
                if SHOW_ARTIFACTS:
                    if t.ref_node and nodes.get(t.ref_node):
                        # link tasks to output artifacts
                        for a in nodes[t.ref_node].output_artifacts:
                            a_id = t.id + "__" + a
                            artifact_lines.append(str(len(links)))
                            links.append(f"{t.id} -.-> {a_id}")
                    # link input artifacts to tasks
                    for a in t.input_artifacts:
                        # for tasks the artifact is fully specified
                        artifact_lines.append(str(len(links)))
                        links.append(f"{a} -.-> {t.id}")

    out = ["graph LR;"]
    out.extend(bases)
    out.extend(links)
    if flow_lines:
        out.append(f"linkStyle {','.join(flow_lines)} stroke:#888,stroke-width:2px;")
    if interpackage_lines:
        out.append(f"linkStyle {','.join(interpackage_lines)} stroke:#888,stroke-width:2px;")
    if artifact_lines:
        out.append(f"linkStyle {','.join(artifact_lines)} stroke:#fa0,stroke-width:2px;")

    mermaid_output = "\n".join(out)
    # print(mermaid_output)
    with open(sys.argv[1], "w") as f:
        f.write("<html><head>")
        f.write("<meta http-equiv='refresh' content='100'/>")
        f.write("<style>pre {margin:0}</style>")
        f.write("<script src=\"https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js\"></script>")
        f.write("</head><body><div class=\"mermaid\">")
        f.write(mermaid_output)
        f.write("</div></body></html>")

def parse_depends(depends):
    if not depends:
        return []
    if isinstance(depends, list):
        return depends
    if not isinstance(depends, str):
        raise ValueError("Invalid depends (not a string): " + str(depends))

    class Tokenizer:
        def __init__(self, input_string):
            for op in ('||', '&&', '!', '(', ')'):
                input_string = input_string.replace(op, ' ' + op + ' ')
            self.tokens = input_string.split()
            self.position = 0

        def consume(self):
            if self.position < len(self.tokens):
                token = self.tokens[self.position]
                self.position += 1
                return token
            else:
                return None

        def peek(self):
            if self.position < len(self.tokens):
                return self.tokens[self.position]
            else:
                return None

    class Parser:
        def __init__(self, tokenizer):
            self.tokenizer = tokenizer
            self.term_prefixes = []

        def pr(self, *args):
            # print(*args)
            pass

        def parse_expression(self):
            token = self.tokenizer.peek()
            if token == '(':
                self.parse_parentheses()
            elif token == '!':
                self.parse_not()
            else:
                self.parse_term()

        def parse_term(self):
            token = self.tokenizer.consume()
            if token is None or token in ('&&', '||', '!', ')'):
                raise ValueError("Unexpected token: " + str(token))
            self.term_prefixes.append(token.split(".")[0])
            self.pr(f"term({token})")

        def parse_parentheses(self):
            self.pr("(")
            self.tokenizer.consume()  # Consume '('
            self.parse_or()
            if self.tokenizer.consume() != ')':
                raise ValueError("Missing closing parenthesis")
            self.pr(")")

        def parse_not(self):
            self.tokenizer.consume()  # Consume 'NOT'
            self.pr("not ")
            self.parse_expression()

        def parse_and(self):
            left = self.parse_expression()
            while self.tokenizer.peek() == '&&':
                self.pr(" and ")
                self.tokenizer.consume()  # Consume 'AND'
                self.parse_expression()

        def parse_or(self):
            left = self.parse_and()
            while self.tokenizer.peek() == '||':
                self.pr(" or ")
                self.tokenizer.consume()  # Consume 'OR'
                self.parse_and()

    tokenizer = Tokenizer(depends)
    parser = Parser(tokenizer)
    parser.parse_or()
    return list(dict.fromkeys(parser.term_prefixes))


def parse_yaml(file_path):
    with open(file_path, 'r') as stream:
        try:
            return yaml.safe_load(stream)
        except yaml.YAMLError as exc:
            print(exc)
            return None

if __name__ == "__main__":
    main()
