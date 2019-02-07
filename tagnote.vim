command -nargs=* WriteAsDate :call WriteAsDate(<f-args>)
command -nargs=* AddTagAsDate :call AddTagAsDate(<f-args>)

let TAGNOTE_NOTES_DIRECTORY = simplify($HOME . '/notes')

let TAGNOTE_UTC = 0

let TAGNOTE_NOTE_REGEX = '[0-9]\{4}-[0-9]\{2}-[0-9]\{2}_[0-9]\{2}-[0-9]\{2}-[0-9]\{2}.txt'


function s:run_with_error_output(command, error)
  let result = system(a:command)
  if v:shell_error
    echo a:error . l:result
  endif
  return v:shell_error
endfunction

function s:is_note(directory, file)
  return a:directory == g:TAGNOTE_NOTES_DIRECTORY
      \ && a:file =~ '^' . g:TAGNOTE_NOTE_REGEX . '$'
endfunction

function WriteAsDate(...)
  if g:TAGNOTE_UTC
    let filename = system('date -u +%F_%H-%M-%S.txt')
    if v:shell_error
      echo "Could not get timestamp\n\n" . l:filename
      return v:shell_error
    endif
  else
    let filename = strftime('%F_%H-%M-%S.txt')
  endif

  let old_directory = simplify(expand('%:p:h'))
  let old_file = expand('%:t')
  if s:is_note(l:old_directory, l:old_file)
    let prototype = '-p ' . shellescape(l:old_file)
  else
    let prototype = ''
  endif

  let tags = ''
  for arg in a:000
    let tags = l:tags . ' ' . shellescape(l:arg)
  endfor

  execute 'saveas ' . g:TAGNOTE_NOTES_DIRECTORY . '/' . l:filename
  return s:run_with_error_output(
    \'tag -r :0 add ' . l:prototype . ' ' . l:filename . l:tags,
    \"Could not add note\n\n"
    \)
endfunction

function AddTagAsDate(...)
  let tags = ''
  for arg in a:000
    let tags = l:tags . ' ' . shellescape(l:arg)
  endfor

  let directory = simplify(expand('%:p:h'))
  let file = expand('%:t')
  if s:is_note(l:directory, l:file)
    return s:run_with_error_output(
      \'tag -r :0 add ' . l:file . ' ' . l:tags,
      \"Could not add tag to note\n\n"
      \)
  else
    echo "File is not a note; run :W first\n"
    return 1
  endif
endfunction
