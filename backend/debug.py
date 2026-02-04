from jinja2 import Environment, BaseLoader
import json

# 1. Load Data
try:
    with open("master_data.json", "r") as f:
        data = json.load(f)
    print("✅ JSON loaded successfully.")
except Exception as e:
    print(f"❌ JSON Error: {e}")
    exit()

# 2. Load Template
try:
    with open("resume_template.tex", "r") as f:
        template_str = f.read()
    print("✅ Template loaded.")
except Exception as e:
    print(f"❌ File Error: {e}")
    exit()

# 3. Test Render
print("🔄 Attempting to compile...")
try:
    env = Environment(
        block_start_string='\\BLOCK{',
        block_end_string='}',
        variable_start_string='\\VAR{',
        variable_end_string='}',
        comment_start_string='\\#{',
        comment_end_string='}',
        loader=BaseLoader()
    )
    template = env.from_string(template_str)
    output = template.render(**data)
    print("✅ SUCCESS! Template compiled perfectly.")
except Exception as e:
    print("\n❌ TEMPLATE ERROR:")
    print(e)