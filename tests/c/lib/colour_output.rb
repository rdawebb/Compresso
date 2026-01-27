# Compresso - C Test Suite Colour Output

module ColourOutput
  # ANSI color codes
  COLORS = {
    default: 39,
    black: 30,
    red: 31,
    green: 32,
    yellow: 33,
    blue: 34,
    magenta: 35,
    cyan: 36,
    white: 37
  }.freeze

  # Define semantic color mappings
  SEMANTIC = {
    success: :green,
    failure: :red,
    warning: :yellow,
    info: :blue,
    debug: :cyan
  }.freeze

  def self.colorize(text, color)
    color_code = COLORS[color] || COLORS[:default]
    if $stdout.tty? && ENV['NO_COLOR'].nil?
      "\033[#{color_code}m#{text}\033[0m"
    else
      text
    end
  end

  def self.puts_colored(text, color = :default)
    puts colorize(text, color)
  end

  def self.print_colored(text, color = :default)
    print colorize(text, color)
  end

  def self.report_line(line)
    color = case line
            when /(?:total\s+)?tests:?\s+(\d+)\s+(?:total\s+)?failures:?\s+\d+\s+Ignored:?/i
              Regexp.last_match(1).to_i.zero? ? :green : :red
            when /PASS/
              :green
            when /^OK$/
              :green
            when /(?:FAIL|ERROR)/
              :red
            when /IGNORE/
              :yellow
            when /^(?:Creating|Compiling|Linking)/
              :white
            when /\[.*\]/
              :cyan
            else
              :default
            end
    puts_colored(line, color)
  end

  def self.report(message)
    message = message.join("\n") if message.is_a?(Array)
    message.each_line do |line|
      report_line(line.chomp)
    end
    $stdout.flush
    $stderr.flush
  end
end
