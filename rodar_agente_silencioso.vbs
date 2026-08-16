Set oShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
oShell.CurrentDirectory = fso.GetParentFolderName(WScript.ScriptFullName)
oShell.Run "cmd /c python reels_para_shorts.py", 0, False
