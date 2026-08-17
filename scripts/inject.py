"""
把新的 DATA dict 寫回 index.html（以及其他要同步的 html 檔案），
方法：用「大括號配對」找出 `const DATA = { ... };` 的精確字元範圍，整段替換成新 JSON，
不用 regex（regex 對這種巢狀又含字串的大型 JSON 容易切錯）。
"""
import json


def find_data_block(html: str):
    idx = html.find("const DATA")
    if idx == -1:
        raise RuntimeError("找不到 const DATA，檔案格式可能已變更")
    eq = html.find("=", idx)
    start = html.find("{", eq)
    depth = 0
    in_str = False
    str_char = ""
    esc = False
    i = start
    while i < len(html):
        c = html[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == str_char:
                in_str = False
        else:
            if c in "\"'":
                in_str = True
                str_char = c
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
        i += 1
    end = i + 1
    return start, end


def inject(html: str, new_data: dict) -> str:
    start, end = find_data_block(html)
    new_json = json.dumps(new_data, ensure_ascii=False, indent=2)
    return html[:start] + new_json + html[end:]


def validate_js(html_path: str):
    """用 Node 的 vm 模組載入新檔案，確認 JS 語法沒壞掉。GitHub Action 裡需要先 `npm install`
    不需要，因為只用內建 vm 模組，只要 Action runner 有 node 即可（ubuntu-latest 內建）。"""
    import subprocess
    import tempfile
    script = """
const fs=require('fs'),vm=require('vm');
const c=fs.readFileSync(process.argv[2],'utf8');
const idx = c.indexOf('const DATA');
const s=c.indexOf('<script>',idx-2000);
const src=c.slice(s+8,c.indexOf('</script>',s));
const ctx={window:{},document:{createElement:()=>({style:{},setAttribute:()=>{},addEventListener:()=>{}}),
  getElementById:()=>({style:{},addEventListener:()=>{},getContext:()=>({})}),querySelector:()=>null,
  querySelectorAll:()=>[],addEventListener:()=>{},body:{appendChild:()=>{}}},
  sessionStorage:{getItem:()=>null,setItem:()=>{}},localStorage:{getItem:()=>null,setItem:()=>{}},
  Chart:function(){this.destroy=()=>{}},XLSX:{utils:{},writeFile:()=>{}},navigator:{},requestAnimationFrame:()=>{},
  setTimeout:()=>{},clearTimeout:()=>{},setInterval:()=>{},clearInterval:()=>{},console:console};
ctx.window=ctx;
vm.createContext(ctx);
vm.runInContext(src+';globalThis.__D=DATA;',ctx,{timeout:8000});
console.log('JS_OK date='+ctx.__D.date);
"""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(script)
        script_path = f.name
    result = subprocess.run(["node", script_path, html_path], capture_output=True, text=True)
    if result.returncode != 0 or "JS_OK" not in result.stdout:
        raise RuntimeError("JS 驗證失敗：{}\n{}".format(result.stdout, result.stderr))
    return result.stdout.strip()
