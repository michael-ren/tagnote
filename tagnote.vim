command -nargs=* WriteAsDate :call WriteAsDate(<f-args>)

let TAGNOTE_NOTES_DIRECTORY = simplify($HOME . '/notes')

let TAGNOTE_UTC = 0

let TAGNOTE_NOTE_REGEX = '[0-9]\{4}-[0-9]\{2}-[0-9]\{2}_[0-9]\{2}-[0-9]\{2}-[0-9]\{2}.txt'

function WriteAsDate(...)
  if g:TAGNOTE_UTC
    let filename = system('date -u +%F_%H-%M-%S.txt')
    if v:shell_error
      echo "Could not get timestamp\n\n" . filename
      return
    endif
  else
    let filename = strftime('%F_%H-%M-%S.txt')
  endif

  let old_directory = simplify(expand('%:p:h'))
  let old_file = expand('%:t')
  if l:old_directory == g:TAGNOTE_NOTES_DIRECTORY
      \ && l:old_file =~ '^' . g:TAGNOTE_NOTE_REGEX . '$'
    let prototype = '-p ' . shellescape(l:old_file)
  else
    let prototype = ''
  endif

  let tags = ''
  for arg in a:000
    let tags = l:tags . ' ' . shellescape(l:arg)
  endfor

  execute 'saveas ' . g:TAGNOTE_NOTES_DIRECTORY . '/' . l:filename
  let result = system('tag -r :0 add ' . l:prototype . ' ' . l:filename . l:tags)
  if v:shell_error
    echo "Could not add note\n\n" . result
    return
  endif
endfunction

