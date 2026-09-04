"""Add <think>...</think> split + collapsible details block to ChatThread."""
import os
path = r"D:\software_bak\Kairos_code\web\src\components\ChatThread.tsx"
t = open(path, encoding="utf-8").read()
if "splitThinking" in t:
    print("already patched")
else:
    # Step 1: insert splitThinking helper right before AssistantBubble.
    old1 = "// R38.6.3: new \"AssistantBubble\""
    new1 = (
        "function splitThinking(text: string): { thinking: string; body: string } {\n"
        "  if (!text) return { thinking: '', body: text };\n"
        "  const m = text.match(/<think>([\\s\\S]*?)<\\/think>/i);\n"
        "  if (!m) return { thinking: '', body: text };\n"
        "  return { thinking: (m[1] || '').trim(),\n"
        "           body: text.replace(m[0], '').trim() };\n"
        "}\n\n"
        "// R38.6.3: new \"AssistantBubble\""
    )
    if old1 in t:
        t = t.replace(old1, new1, 1)
        print("step 1: splitThinking helper inserted")
    else:
        print("FAIL step 1: anchor not found")

    # Step 2: replace the `const text = stringifyContent` with destructured
    old2 = "  const text = stringifyContent(message.content);"
    new2 = "  const { thinking, body } = splitThinking(stringifyContent(message.content));"
    if old2 in t:
        t = t.replace(old2, new2, 1)
        print("step 2: text -> thinking/body destructure")
    else:
        print("FAIL step 2: anchor not found")

    # Step 3: replace the `{text}` rendering with the thinking details
    # block followed by the body span. The current rendering is the
    # two-line <span> inside AssistantBubble.
    old3 = (
        "        <span style={{\n"
        "          fontSize: 13, color: tokens.labelPrimary,\n"
        "          whiteSpace: 'pre-wrap', wordBreak: 'break-word',\n"
        "          lineHeight: 1.6,\n"
        "        }}>{text}</span>\n"
    )
    new3 = (
        "        {thinking && (\n"
        "          <details\n"
        "            data-testid=\"think-block\"\n"
        "            style={{\n"
        "              marginBottom: 6, background: tokens.bgLay1,\n"
        "              border: `1px solid ${tokens.border}`,\n"
        "              borderRadius: 6, padding: '4px 8px',\n"
        "            }}\n"
        "          >\n"
        "            <summary style={{\n"
        "              cursor: 'pointer', fontSize: 11,\n"
        "              color: tokens.labelTertiary, userSelect: 'none',\n"
        "            }}>\n"
        "              💭 思考过程（点击展开）\n"
        "            </summary>\n"
        "            <div style={{\n"
        "              fontSize: 12, color: tokens.labelSecondary,\n"
        "              lineHeight: 1.6, marginTop: 4,\n"
        "              whiteSpace: 'pre-wrap', wordBreak: 'break-word',\n"
        "            }}>\n"
        "              {thinking}\n"
        "            </div>\n"
        "          </details>\n"
        "        )}\n"
        "        {body && (\n"
        "          <span style={{\n"
        "            fontSize: 13, color: tokens.labelPrimary,\n"
        "            whiteSpace: 'pre-wrap', wordBreak: 'break-word',\n"
        "            lineHeight: 1.6,\n"
        "          }}>{body}</span>\n"
        "        )}\n"
    )
    if old3 in t:
        t = t.replace(old3, new3, 1)
        print("step 3: thinking details + body span")
    else:
        print("FAIL step 3: anchor not found")

    open(path, "w", encoding="utf-8").write(t)
    print("done")
