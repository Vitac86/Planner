Set oFSO = CreateObject("Scripting.FileSystemObject")
Set oShell = CreateObject("WScript.Shell")
base = oFSO.GetParentFolderName(WScript.ScriptFullName)
oShell.CurrentDirectory = base
oShell.Run """" & base & "\start_planner.bat""", 0, False
