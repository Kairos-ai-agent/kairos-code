from kairos.tools.file_read import FileReadTool
t = FileReadTool(allowed_root="/tmp")
print("attrs:", [a for a in dir(t) if not a.startswith("_")])
